from __future__ import annotations

import logging
import threading
from typing import Protocol

from orchestrator_core.schemas import StartJobCommand
from orchestrator_core.service import OrchestratorCore

logger = logging.getLogger(__name__)


class WorkerGateway(Protocol):
    """
    Контракт Dispatcher -> Worker (реализуется в Блоке 04).
    Метод должен принять команду StartJobCommand к асинхронному/фоновому исполнению.
    Если передача подтверждённо сорвалась до принятия Worker, метод выбрасывает исключение.
    """

    def dispatch(self, command: StartJobCommand) -> None:
        ...


class Dispatcher:
    """
    Выполняет цикл диспетчеризации:
    - после каждого успешного резервирования передаёт задачу Worker вне транзакции БД
      и повторяет проверку с начала упорядоченного списка кандидатов;
    - при подтверждённом отказе передачи переводит STARTING -> FAILED (DISPATCH_ERROR).
    """

    def __init__(self, core: OrchestratorCore, worker_gateway: WorkerGateway) -> None:
        self.core = core
        self.worker_gateway = worker_gateway

    def run_pass(self) -> list[StartJobCommand]:
        dispatched: list[StartJobCommand] = []
        while True:
            command = self.core.dispatch_single_step()
            if command is None:
                break

            try:
                self.worker_gateway.dispatch(command)
                dispatched.append(command)
            except Exception as exc:
                logger.exception(
                    "Dispatch handoff failed for job_id=%s execution_id=%s",
                    command.job_id,
                    command.execution_id,
                )
                self.core.fail_dispatch(
                    job_id=command.job_id,
                    execution_id=command.execution_id,
                    reason=f"Worker handoff failed before acceptance: {exc}",
                )
        return dispatched


class DispatcherRunner:
    """
    Фоновый поток диспетчера с коалесценцией сигналов пробуждения:
    если сигнал пришёл во время прохода, следующий проход запустится сразу перед сном.
    """

    def __init__(self, dispatcher: Dispatcher) -> None:
        self.dispatcher = dispatcher
        self._cond = threading.Condition()
        self._dirty = False
        self._stop = False
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop = False
        self._dirty = True
        self.dispatcher.core.set_wakeup_callback(self.notify)
        self._thread = threading.Thread(
            target=self._loop, name="orchestrator-dispatcher", daemon=True
        )
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        with self._cond:
            self._stop = True
            self._cond.notify_all()
        if self._thread is not None:
            self._thread.join(timeout=timeout)
        self.dispatcher.core.set_wakeup_callback(None)

    def notify(self) -> None:
        with self._cond:
            self._dirty = True
            self._cond.notify_all()

    def _loop(self) -> None:
        while True:
            with self._cond:
                while not self._dirty and not self._stop:
                    self._cond.wait()
                if self._stop:
                    return
                self._dirty = False

            try:
                self.dispatcher.run_pass()
            except Exception:
                logger.exception("Unhandled error inside dispatcher pass")
