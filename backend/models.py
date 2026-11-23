from __future__ import annotations

import datetime as dt
from typing import Any

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class OrchestratorControl(Base):
    """
    Управляющая строка (id=1) для сериализации критических секций в PostgreSQL
    через SELECT ... FOR UPDATE и монотонной выдачи queueSequence / eventId.
    """

    __tablename__ = "orchestrator_control"
    __table_args__ = (CheckConstraint("id = 1", name="ck_control_singleton"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, default=1)
    last_queue_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)
    last_event_id: Mapped[int] = mapped_column(BigInteger, nullable=False, default=0)


class ResourceModel(Base):
    """Реестр логических ресурсов."""

    __tablename__ = "resources"

    resource_key: Mapped[str] = mapped_column(String(128), primary_key=True)
    description: Mapped[str] = mapped_column(Text, nullable=False)
    partitioning: Mapped[str] = mapped_column(String(16), nullable=False)  # BY_DATE | GLOBAL


class ProcessModel(Base):
    """Реестр процессов оценки рисков и их шаблонов доступов."""

    __tablename__ = "processes"

    process_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    worker_handler: Mapped[str] = mapped_column(String(128), nullable=False)
    access_templates_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)


class JobModel(Base):
    """Задачи в единой очереди и их история."""

    __tablename__ = "jobs"
    __table_args__ = (
        Index("ix_jobs_status_queue_seq", "status", "queue_sequence"),
        Index("ix_jobs_process_date", "process_id", "calculation_date"),
    )

    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    process_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("processes.process_id"), nullable=False
    )
    calculation_date: Mapped[str] = mapped_column(String(10), nullable=False)

    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)  # MANUAL | SCHEDULED
    initiator_id: Mapped[str] = mapped_column(String(128), nullable=False)
    schedule_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    scheduled_for: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    queue_sequence: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    queued_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    started_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    finished_at: Mapped[dt.datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    accesses_json: Mapped[list[dict[str, Any]]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    execution_id: Mapped[str | None] = mapped_column(String(36), nullable=True)

    wait_reason_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    result_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)
    error_json: Mapped[dict[str, Any] | None] = mapped_column(JSON, nullable=True)


class IdempotencyRecordModel(Base):
    """
    Связь ключа идемпотентности (для MANUAL) или срабатывания регламента (для SCHEDULED)
    с параметрами заявки и job_id.
    """

    __tablename__ = "idempotency_records"
    __table_args__ = (
        UniqueConstraint(
            "trigger_type",
            "scope_actor",
            "idempotency_key",
            name="uq_idempotency_scope_key",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    trigger_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_actor: Mapped[str] = mapped_column(String(128), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(256), nullable=False)
    process_id: Mapped[str] = mapped_column(String(128), nullable=False)
    calculation_date: Mapped[str] = mapped_column(String(10), nullable=False)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.job_id"), nullable=False, unique=True
    )
    created_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ResourceReservationModel(Base):
    """Активные резервирования ресурсов для задач в статусах STARTING и RUNNING."""

    __tablename__ = "resource_reservations"
    __table_args__ = (
        Index("ix_reservations_resource_key", "resource_key"),
        Index("ix_reservations_job_id", "job_id"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.job_id", ondelete="CASCADE"), nullable=False
    )
    execution_id: Mapped[str] = mapped_column(String(36), nullable=False)
    resource_key: Mapped[str] = mapped_column(
        String(128), ForeignKey("resources.resource_key"), nullable=False
    )
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)  # DATE | GLOBAL
    scope_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    mode: Mapped[str] = mapped_column(String(8), nullable=False)  # READ | WRITE
    reserved_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class JobEventModel(Base):
    """Журнал всех переходов состояния и изменений причины ожидания."""

    __tablename__ = "job_events"
    __table_args__ = (Index("ix_job_events_job_id_event_id", "job_id", "event_id"),)

    event_id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=False)
    job_id: Mapped[str] = mapped_column(
        String(36), ForeignKey("jobs.job_id"), nullable=False
    )
    type: Mapped[str] = mapped_column(String(32), nullable=False)
    occurred_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    payload_json: Mapped[dict[str, Any]] = mapped_column(JSON, nullable=False, default=dict)


class ResourceValueModel(Base):
    """
    Последнее успешно опубликованное значение ресурса в заданной области.
    Обновляется атомарно с переходом задачи в SUCCEEDED.
    """

    __tablename__ = "resource_values"

    resource_key: Mapped[str] = mapped_column(
        String(128), ForeignKey("resources.resource_key"), primary_key=True
    )
    scope_key: Mapped[str] = mapped_column(String(32), primary_key=True)
    scope_type: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_date: Mapped[str | None] = mapped_column(String(10), nullable=True)
    value_json: Mapped[Any] = mapped_column(JSON, nullable=False)
    updated_by_job_id: Mapped[str | None] = mapped_column(
        String(36), ForeignKey("jobs.job_id"), nullable=True
    )
    updated_at: Mapped[dt.datetime] = mapped_column(DateTime(timezone=True), nullable=False)
