from datetime import date
from uuid import UUID, uuid4

from fastapi import APIRouter, Request, Response
from pydantic import Field

from backend.api.models import Empty, Failure, IdempotencyKey, Receipt
from backend.orchestrator.batches import batch_record, create_batch
from backend.orchestrator.isolation import job_storage
from backend.orchestrator.snapshots import HANDLER_VERSION
from backend.orchestrator.storage import DomainError, encode, query

router = APIRouter(prefix="/api/v1")


class BatchRequest(Empty):
    calculationDate: date
    factorRevision: int = Field(ge=1)


@router.get("/factor-revisions")
def factors(request: Request):
    with request.app.state.runtime.storage.engine.connect() as conn:
        return {"items": list(query(conn, "SELECT * FROM factor_history ORDER BY revision DESC").mappings())}


@router.post("/batches", status_code=201)
def batch(body: BatchRequest, request: Request):
    return create_batch(
        request.app.state.runtime,
        body.calculationDate,
        body.factorRevision,
        request.state.actor,
    )


@router.get("/batches/{batch_id}")
def read_batch(batch_id: UUID, request: Request):
    with request.app.state.runtime.storage.engine.connect() as conn:
        return batch_record(conn, batch_id)


@router.get("/jobs/{job_id}/snapshot")
def snapshot(job_id: UUID, request: Request):
    with job_storage(request.app.state.runtime, job_id).engine.connect() as conn:
        row = query(conn, "SELECT snapshot FROM jobs WHERE id=:id", id=job_id).first()
        if not row or not row.snapshot:
            raise DomainError("SNAPSHOT_UNAVAILABLE", "Снимок входов недоступен", 404)
        return row.snapshot


@router.post(
    "/jobs/{job_id}/replay",
    status_code=201,
    response_model=Receipt,
    responses={200: {"model": Receipt}, 409: {"model": Failure}},
)
def replay(job_id: UUID, body: Empty, request: Request, response: Response, key: IdempotencyKey = None):
    runtime = request.app.state.runtime
    actor = request.state.actor
    delivery = encode(["REPLAY", actor, key]) if key else None
    with runtime.storage.transaction() as conn:
        if delivery:
            existing = query(
                conn,
                """SELECT j.id,j.status,j.queued,j.replay_of FROM deliveries d
                   JOIN jobs j ON j.id=d.job_id WHERE d.key=:key""",
                key=delivery,
            ).first()
            if existing:
                if existing.replay_of != job_id:
                    raise DomainError("IDEMPOTENCY_CONFLICT", "Ключ уже связан с другими параметрами")
                request.state.job_id = str(existing.id)
                response.status_code = 200
                return {"jobId": existing.id, "status": existing.status, "queuedAt": existing.queued}
        old = query(conn, "SELECT * FROM jobs WHERE id=:id", id=job_id).first()
        if not old or not old.result or not old.snapshot:
            raise DomainError(
                "SNAPSHOT_UNAVAILABLE",
                "Для воспроизведения нужен завершённый расчёт со снимком",
            )
        if old.snapshot["handlerVersion"] != HANDLER_VERSION:
            raise DomainError("HANDLER_VERSION_UNAVAILABLE", "Версия обработчика из снимка недоступна")
        row = query(
            conn,
            """INSERT INTO jobs(id,process,day,trigger,accesses,status,version,priority,
              handler,bindings,snapshot,replay_of)
              SELECT :new,process,day,CAST(:t AS jsonb),accesses,'QUEUED',version,priority,handler,bindings,
              snapshot,id
              FROM jobs WHERE id=:id RETURNING *""",
            new=uuid4(),
            t=encode({"type": "MANUAL", "initiatorId": actor}),
            id=job_id,
        ).one()
        if delivery:
            query(conn, "INSERT INTO deliveries(key,job_id) VALUES (:key,:id)", key=delivery, id=row.id)
        runtime.jobs.event(conn, row, "JOB_QUEUED", {"replayOf": str(job_id)})
    runtime.wake.set()
    request.state.job_id = str(row.id)
    return {"jobId": row.id, "status": row.status, "queuedAt": row.queued}


@router.get("/schedule-history")
def schedule_history(request: Request, before: int | None = None):
    with request.app.state.runtime.storage.engine.connect() as conn:
        return {
            "items": list(
                query(
                    conn,
                    """SELECT * FROM schedule_history
            WHERE id < coalesce(:before,9223372036854775807) ORDER BY id DESC LIMIT 100""",
                    before=before,
                ).mappings()
            )
        }


@router.get("/benchmarks")
def benchmarks(request: Request, compact: bool = False):
    with request.app.state.runtime.storage.engine.connect() as conn:
        rows = (
            query(
                conn,
                "SELECT * FROM benchmark_results ORDER BY created DESC LIMIT :limit",
                limit=1 if compact else 10,
            )
            .mappings()
            .all()
        )
    if compact:
        for row in rows:
            for pair in row["report"]["pairs"]:
                for result in pair.values():
                    result.pop("events", None)
                    result.pop("jobs", None)
    return {"items": rows}
