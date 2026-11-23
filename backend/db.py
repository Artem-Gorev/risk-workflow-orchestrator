from __future__ import annotations

import threading
from contextlib import contextmanager
from typing import Iterator

from sqlalchemy import Engine, create_engine, select
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from orchestrator_core.models import Base, OrchestratorControl


class Database:
    """
    Обёртка над SQLAlchemy Engine/Session.
    В PostgreSQL сериализует критические транзакции через SELECT ... FOR UPDATE
    на строке orchestrator_control(id=1). В SQLite (для локальных unit-тестов)
    дополнительно использует threading.RLock.
    """

    def __init__(self, database_url: str, *, echo: bool = False) -> None:
        engine_kwargs: dict = {
            "echo": echo,
            "future": True,
            "pool_pre_ping": True,
        }
        if database_url.startswith("sqlite"):
            engine_kwargs["connect_args"] = {"check_same_thread": False}
            if ":memory:" in database_url:
                engine_kwargs["poolclass"] = StaticPool

        self.engine: Engine = create_engine(database_url, **engine_kwargs)
        self.session_factory = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            autoflush=False,
        )
        self._is_sqlite = self.engine.dialect.name == "sqlite"
        self._sqlite_lock = threading.RLock()

    def create_schema_and_bootstrap(self) -> None:
        """Создаёт таблицы (для тестов / dev) и гарантирует наличие управляющей строки."""
        Base.metadata.create_all(self.engine)
        self.ensure_control_row()

    def ensure_control_row(self) -> None:
        with self.session() as session:
            existing = session.get(OrchestratorControl, 1)
            if existing is None:
                session.add(
                    OrchestratorControl(id=1, last_queue_sequence=0, last_event_id=0)
                )
                session.commit()

    @contextmanager
    def session(self) -> Iterator[Session]:
        session = self.session_factory()
        try:
            yield session
        finally:
            session.close()

    @contextmanager
    def serialized_transaction(self) -> Iterator[tuple[Session, OrchestratorControl]]:
        """
        Короткая ACID-транзакция с эксклюзивной блокировкой управляющей строки.
        Гарантирует отсутствие гонок при проверке/резервировании ресурсов, отмене и завершении.
        """
        if self._is_sqlite:
            with self._sqlite_lock:
                with self._run_locked_tx() as pair:
                    yield pair
        else:
            with self._run_locked_tx() as pair:
                yield pair

    @contextmanager
    def _run_locked_tx(self) -> Iterator[tuple[Session, OrchestratorControl]]:
        session = self.session_factory()
        try:
            stmt = select(OrchestratorControl).where(OrchestratorControl.id == 1)
            if not self._is_sqlite:
                stmt = stmt.with_for_update()
            control = session.execute(stmt).scalar_one_or_none()
            if control is None:
                control = OrchestratorControl(id=1, last_queue_sequence=0, last_event_id=0)
                session.add(control)
                session.flush()
            yield session, control
            session.commit()
        except Exception:
            session.rollback()
            raise
        finally:
            session.close()
