import os
from dataclasses import dataclass, field
from math import isfinite


@dataclass(frozen=True, slots=True)
class BackendConfig:
    database_url: str = field(repr=False)
    worker_slots: int = 2
    dispatcher_poll: float = 1
    scheduler_poll: float = 1
    worker_delay: float = 0
    daily_enabled: bool = False
    lock_path: str = "/tmp/risk-orchestrator.lock"
    auth_mode: str = "local"
    demo_initiator: str = "demo"
    public_origin: str = ""

    def __post_init__(self) -> None:
        intervals = (self.dispatcher_poll, self.scheduler_poll, self.worker_delay)
        if (
            self.worker_slots < 1
            or not all(map(isfinite, intervals))
            or min(self.dispatcher_poll, self.scheduler_poll) <= 0
            or self.worker_delay < 0
        ):
            raise ValueError("Проверьте размер пула, задержку расчёта и интервалы опроса")
        if self.auth_mode not in ("local", "proxy"):
            raise ValueError("AUTH_MODE должен быть local или proxy")
        if self.auth_mode == "proxy" and not self.public_origin:
            raise ValueError("Для режима proxy задайте PUBLIC_ORIGIN")

    @classmethod
    def from_env(cls) -> "BackendConfig":
        return cls(
            database_url=os.environ["DATABASE_URL"],
            worker_slots=int(os.getenv("WORKER_SLOTS", "2")),
            dispatcher_poll=float(os.getenv("DISPATCH_POLL_SECONDS", "1")),
            scheduler_poll=float(os.getenv("SCHEDULER_POLL_SECONDS", "1")),
            worker_delay=float(os.getenv("WORKER_DELAY_SECONDS", "0")),
            daily_enabled=os.getenv("DAILY_ENABLED", "0") == "1",
            lock_path=os.getenv("BACKEND_LOCK_PATH", "/tmp/risk-orchestrator.lock"),
            auth_mode=os.getenv("AUTH_MODE", "local"),
            demo_initiator=os.getenv("DEMO_INITIATOR", "demo"),
            public_origin=os.getenv("PUBLIC_ORIGIN", "").rstrip("/"),
        )
