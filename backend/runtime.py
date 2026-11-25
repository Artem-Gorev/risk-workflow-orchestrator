import logging
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event, Thread
from time import monotonic
from uuid import UUID

from backend.config import BackendConfig
from backend.orchestrator.batches import advance
from backend.orchestrator.service import JobService
from backend.orchestrator.storage import Storage, query
from backend.scheduler.service import next_daily, tick
from backend.workers.executor import Worker

log = logging.getLogger("uvicorn.error")


class Runtime:
    def __init__(self, config: BackendConfig):
        self.config = config
        self.storage = Storage(config.database_url)
        self.jobs = JobService(self.storage, config.worker_slots)
        self.pool = ThreadPoolExecutor(config.worker_slots, thread_name_prefix="risk-worker")
        self.stop = Event()
        self.wake = Event()
        self.worker = Worker(
            self.jobs,
            config.worker_delay,
            self.stop,
            self.wake,
            self.config.dispatcher_poll,
        )
        self.thread = Thread(target=self.run, name="dispatcher")
        self.healthy = False
        self.in_flight: dict[UUID, Future[None]] = {}
        self.demo: Runtime | None = None

    def open(self):
        self.storage.seed()
        self.jobs.recover()
        with self.storage.transaction() as conn:
            if self.config.daily_enabled:
                query(
                    conn,
                    """INSERT INTO schedules(id,process,due,daily) VALUES ('credit-daily','credit-risk',:due,
                        true)
                               ON CONFLICT DO NOTHING""",
                    due=next_daily(datetime.now(UTC)),
                )
            else:
                query(conn, "DELETE FROM schedules WHERE id='credit-daily' AND daily")
        self.healthy = True
        self.thread.start()

    def close(self):
        self.stop.set()
        self.wake.set()
        if self.thread.ident is not None:
            self.thread.join()
        self.pool.shutdown(wait=True)
        self.worker.compute.close()
        self.storage.engine.dispose()

    def dispatch(self, job):
        if job.execution in self.in_flight:
            return
        try:
            self.in_flight[job.execution] = self.pool.submit(self.worker.execute, job)
        except RuntimeError:
            with self.storage.transaction() as conn:
                self.jobs.fail(conn, job, "DISPATCH_ERROR", "Исполнитель не принял задание")

    def resume_dispatch(self):
        self.in_flight = {key: future for key, future in self.in_flight.items() if not future.done()}
        with self.storage.engine.connect() as conn:
            pending = query(conn, "SELECT * FROM jobs WHERE status='STARTING' ORDER BY seq").all()
        for job in pending:
            self.dispatch(job)

    def run(self):
        next_tick = 0.0
        while not self.stop.is_set():
            self.wake.clear()
            try:
                if monotonic() >= next_tick:
                    tick(self.jobs, datetime.now(UTC))
                    next_tick = monotonic() + self.config.scheduler_poll
                advance(self.jobs)
                self.resume_dispatch()
                while not self.stop.is_set() and (job := self.jobs.reserve()):
                    self.dispatch(job)
                self.healthy = True
            except Exception:
                self.healthy = False
                log.exception("Ошибка диспетчеризации")
            self.wake.wait(min(self.config.dispatcher_poll, self.config.scheduler_poll, 1))
