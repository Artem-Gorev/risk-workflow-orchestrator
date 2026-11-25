import logging

from sqlalchemy.exc import SQLAlchemyError

from backend.logging import record
from backend.orchestrator.snapshots import execution_inputs
from backend.orchestrator.storage import DomainError, query
from backend.workers.pool import CalculationPool, CalculationTimeout

log = logging.getLogger("uvicorn.error")


class Worker:
    def __init__(self, jobs, delay, stop, wake, poll, timeout):
        self.compute = CalculationPool(timeout)
        self.jobs = jobs
        self.storage = jobs.storage
        self.delay = delay
        self.stop = stop
        self.wake = wake
        self.poll = poll

    def execute(self, job):
        code, message = "EXECUTION_ERROR", "Ошибка выполнения расчёта"
        try:
            if not self.jobs.start(job):
                return
            with self.storage.transaction() as conn:
                active = query(
                    conn,
                    """SELECT * FROM jobs WHERE id=:id AND execution=:execution
                               AND status='RUNNING' AND lease_until>clock_timestamp()""",
                    id=job.id,
                    execution=job.execution,
                ).first()
                if not active:
                    return
                inputs, versions = execution_inputs(conn, active)
            outputs = self.compute.calculate(
                job.handler or job.process,
                inputs,
                self.delay + job.duration,
                lambda: not self.stop.is_set() and self.jobs.heartbeat(job),
            )
            if outputs is None:
                if self.stop.is_set():
                    with self.storage.transaction() as conn:
                        self.jobs.fail(conn, job, "EXECUTION_INTERRUPTED", "Исполнитель остановлен")
                return
            outputs = {job.bindings.get(key, key): value for key, value in outputs.items()}
            code, message = "RESULT_WRITE_ERROR", "Ошибка публикации результата"
            self.jobs.complete(job, outputs, versions)
            return
        except (BrokenPipeError, EOFError, ConnectionResetError):
            code, message = "WORKER_LOST", "Прервалась связь с исполнителем"
        except CalculationTimeout:
            code, message = "EXECUTION_TIMEOUT", "Исполнитель не ответил в отведённое время"
        except DomainError as error:
            code, message = error.code, str(error)
        except Exception:
            log.exception("Сбой jobId=%s executionId=%s code=%s", job.id, job.execution, code)
        finally:
            self.wake.set()
        record("execution_error", jobId=job.id, executionId=job.execution, code=code)
        while True:
            try:
                with self.storage.transaction() as conn:
                    self.jobs.fail(conn, job, code, message)
                self.wake.set()
                return
            except SQLAlchemyError:
                if self.stop.wait(self.poll):
                    return
