from datetime import UTC, datetime, time
from typing import Annotated, Literal
from uuid import UUID

from fastapi import APIRouter, Request
from pydantic import Field, model_validator

from backend.api.models import (
    CONFLICT_ERRORS,
    Cancellation,
    Empty,
    Failure,
    IdempotencyKey,
    ProcessInfo,
    Receipt,
    ScheduleRecord,
)
from backend.orchestrator.catalog import latest, save_process
from backend.orchestrator.storage import DomainError, encode, query, timestamp
from backend.scheduler.service import deliver_due_slots, next_slot
from backend.workers.catalog import PROCESSES

router = APIRouter(prefix="/api/v1")
Code = Annotated[str, Field(pattern=r"^[a-z][a-z0-9_-]{0,63}$")]


def audit(conn, request, operation, object_id, details):
    query(
        conn,
        "INSERT INTO configuration_events(actor,operation,object_id,details) "
        "VALUES (:actor,:op,:id,CAST(:details AS jsonb))",
        actor=request.state.actor,
        op=operation,
        id=object_id,
        details=encode(details),
    )


@router.get("/audit/configuration")
def audit_log(request: Request, afterId: int = 0):
    with request.app.state.runtime.storage.engine.connect() as conn:
        rows = query(
            conn,
            "SELECT * FROM configuration_events WHERE id>:id ORDER BY id LIMIT 100",
            id=afterId,
        ).mappings()
        return {"items": [dict(row) | {"occurred": timestamp(row["occurred"])} for row in rows]}


class Definition(Empty):
    joinActive: bool = False
    handler: Code
    name: str = Field(min_length=1, max_length=120)
    description: str = Field(default="", max_length=1000)
    priority: int = Field(default=5, ge=1, le=9)
    executionSeconds: float = Field(default=0, ge=0, le=300)
    isActive: bool = True
    bindings: dict[Code, Code] = Field(default_factory=dict)
    expectedVersion: int = Field(default=0, ge=0)


@router.put("/processes/{code}", response_model=ProcessInfo, responses=CONFLICT_ERRORS)
def definition(code: Code, body: Definition, request: Request):
    with request.app.state.runtime.storage.transaction() as conn:
        result = save_process(conn, code, body, body.expectedVersion)
        audit(conn, request, "PROCESS_SAVED", code, result)
        return result


@router.get("/handlers")
def handlers(request: Request):
    with request.app.state.runtime.storage.engine.connect() as conn:
        return {
            "items": [
                {"processId": p.code, "name": p.name, "accessTemplates": p.templates()}
                for p in PROCESSES.values()
            ],
            "resources": list(
                query(
                    conn,
                    "SELECT resource_key,coalesce(kind,resource_key) AS kind "
                    "FROM resources ORDER BY resource_key",
                ).mappings()
            ),
        }


class Resource(Empty):
    key: Code
    kind: Code
    copyValues: bool = False


@router.post("/resources", status_code=201, responses=CONFLICT_ERRORS)
def resource(body: Resource, request: Request):
    with request.app.state.runtime.storage.transaction() as conn:
        source = query(
            conn,
            "SELECT partitioning FROM resources WHERE resource_key=:kind AND kind IS NULL",
            kind=body.kind,
        ).first()
        if not source:
            raise DomainError("UNKNOWN_RESOURCE_KIND", "Неизвестный тип данных", 422)
        created = query(
            conn,
            """INSERT INTO resources VALUES (:key,:partition,:kind) ON CONFLICT DO NOTHING RETURNING
                resource_key""",
            key=body.key,
            partition=source.partitioning,
            kind=body.kind,
        ).first()
        if not created:
            raise DomainError("RESOURCE_EXISTS", "Ключ ресурса уже занят")
        if body.copyValues:
            if query(
                conn,
                "SELECT 1 FROM reservations WHERE resource_key=:key AND mode='WRITE' LIMIT 1",
                key=body.kind,
            ).first():
                raise DomainError("RESOURCE_CONFLICT", "Исходный ресурс занят записью")
            query(
                conn,
                """INSERT INTO resource_values(key,partition,value,source)
                          SELECT :target,partition,value,source FROM resource_values WHERE key=:source""",
                target=body.key,
                source=body.kind,
            )
        audit(conn, request, "RESOURCE_CREATED", body.key, body.model_dump())
    return body


@router.post(
    "/jobs/{job_id}/stop", response_model=Cancellation, responses={404: {"model": Failure}, **CONFLICT_ERRORS}
)
def stop(job_id: UUID, body: Empty, request: Request):
    runtime = request.app.state.runtime
    runtime.jobs.stop_job(job_id, request.state.actor)
    runtime.wake.set()
    return {"jobId": job_id, "status": "CANCELLED"}


@router.post(
    "/jobs/{job_id}/restart", response_model=Receipt, responses={404: {"model": Failure}, **CONFLICT_ERRORS}
)
def restart(
    job_id: UUID,
    body: Empty,
    request: Request,
    key: IdempotencyKey = None,
):
    runtime = request.app.state.runtime
    with runtime.storage.transaction() as conn:
        old = query(conn, "SELECT * FROM jobs WHERE id=:id", id=job_id).first()
        if not old:
            raise DomainError("JOB_NOT_FOUND", "Задача не найдена", 404)
        if not old.finished:
            raise DomainError("JOB_STATE_CONFLICT", "Сначала остановите задачу")
        if old.batch_id:
            raise DomainError("BATCH_STEP_RESTART", "Чтобы повторить этот расчёт, запустите серию заново")
        trigger = {
            "type": "MANUAL",
            "initiatorId": request.state.actor,
            "restartedFrom": str(job_id),
        }
        row, _ = runtime.jobs.submit(conn, old.process, old.day, trigger, key, old.priority)
    runtime.wake.set()
    return {"jobId": row.id, "status": row.status, "queuedAt": row.queued}


class Schedule(Empty):
    processId: Code
    frequency: Literal["ONCE", "DAILY", "WEEKLY"]
    localTime: str | None = Field(default=None, pattern=r"^(?:[01][0-9]|2[0-3]):[0-5][0-9]$")
    weekday: int | None = Field(default=None, ge=0, le=6)
    enabled: bool = True

    @model_validator(mode="after")
    def validate_day(self):
        if (self.frequency == "WEEKLY") != (self.weekday is not None):
            raise ValueError("День недели нужен для еженедельного расписания")
        if (self.frequency != "ONCE") != (self.localTime is not None):
            raise ValueError("Укажите местное время только для повторяющегося расписания")
        return self


@router.get("/schedules", response_model=dict[str, list[ScheduleRecord]])
def schedules(request: Request):
    with request.app.state.runtime.storage.engine.connect() as conn:
        return {"items": list(query(conn, "SELECT * FROM schedules ORDER BY id").mappings())}


@router.put("/schedules/{code}", responses=CONFLICT_ERRORS)
def schedule(code: Code, body: Schedule, request: Request):
    if code == "credit-daily":
        raise DomainError("MANAGED_SCHEDULE", "Этот регламент задаёт DAILY_ENABLED")
    runtime = request.app.state.runtime
    with runtime.storage.transaction() as conn:
        latest(conn, body.processId)
        existing = query(conn, "SELECT * FROM schedules WHERE id=:id", id=code).first()
        if existing and existing.demo_id:
            raise DomainError("MANAGED_SCHEDULE", "Демонстрационный регламент управляется стендом")
        if (body.frequency == "ONCE") != bool(existing and not existing.daily):
            raise DomainError("SCHEDULE_FREQUENCY_CONFLICT", "Тип одноразового расписания нельзя изменить")

        if body.frequency == "ONCE":
            if existing.enabled and (existing.process != body.processId or not body.enabled):
                deliver_due_slots(runtime.jobs, conn, existing, datetime.now(UTC))
            due = query(
                conn,
                "UPDATE schedules SET process=:process,enabled=:enabled WHERE id=:id RETURNING due",
                id=code, process=body.processId, enabled=body.enabled,
            ).scalar_one()
        else:
            due = save_recurring_schedule(conn, runtime, existing, code, body)
        audit(conn, request, "SCHEDULE_SAVED", code, body.model_dump())
    runtime.wake.set()
    return {"scheduleId": code, "nextFireAt": timestamp(due) if body.enabled else None}


def save_recurring_schedule(conn, runtime, existing, code, body):
    local = time.fromisoformat(body.localTime)
    if existing and existing.day is None and (
        existing.process, existing.local_time, existing.weekday, existing.enabled
    ) == (body.processId, local, body.weekday, body.enabled):
        return existing.due
    now = datetime.now(UTC)
    if existing and existing.enabled:
        deliver_due_slots(runtime.jobs, conn, existing, now)
    due = next_slot(now, local, body.weekday) if body.enabled else None
    query(
        conn,
        """INSERT INTO schedules(id,process,due,daily,local_time,weekday,enabled)
                 VALUES (:id,:p,:due,true,:t,:w,:enabled) ON CONFLICT(id) DO UPDATE SET
                 process=excluded.process,due=excluded.due,daily=true,day=NULL,
                 local_time=excluded.local_time,
                 weekday=excluded.weekday,enabled=excluded.enabled""",
        id=code,
        p=body.processId,
        due=due,
        t=local,
        w=body.weekday,
        enabled=body.enabled,
    )
    return due
