import base64
import json
from datetime import UTC, date, datetime
from typing import Annotated, Literal, get_args
from uuid import UUID

from fastapi import APIRouter, Query, Request, Response

from backend.orchestrator.catalog import process_info
from backend.orchestrator.isolation import job_storage
from backend.orchestrator.storage import (
    DomainError,
    encode,
    job_record,
    query,
    timestamp,
)

from .models import (
    Cancellation,
    Catalog,
    Empty,
    EventPage,
    Failure,
    IdempotencyKey,
    Job,
    JobPage,
    Overview,
    Provenance,
    Receipt,
    Source,
    Status,
    Submission,
)

router = APIRouter(prefix="/api/v1", responses={code: {"model": Failure} for code in (401, 503)})
WRITE_ERRORS = {code: {"model": Failure} for code in (403, 409, 415)}
PageSize = Annotated[int, Query(ge=1, le=200)]


@router.get("/processes", response_model=Catalog)
def processes(request: Request):
    with request.app.state.runtime.storage.engine.connect() as conn:
        rows = query(
            conn,
            "SELECT DISTINCT ON(process_id) * FROM processes ORDER BY process_id,version DESC",
        )
        return {"items": [process_info(row) for row in rows]}


@router.post(
    "/jobs",
    response_model=Receipt,
    status_code=201,
    responses={200: {"model": Receipt}, **WRITE_ERRORS},
)
def submit(
    body: Submission,
    request: Request,
    response: Response,
    key: IdempotencyKey = None,
):
    runtime = request.app.state.runtime
    trigger = {"type": "MANUAL", "initiatorId": request.state.actor}
    with runtime.storage.transaction() as conn:
        job, created = runtime.jobs.submit(
            conn, body.processId, body.calculationDate, trigger, key, body.priority
        )
    runtime.wake.set()
    request.state.job_id = str(job.id)
    response.status_code = 201 if created else 200
    return {"jobId": job.id, "status": job.status, "queuedAt": job.queued}


@router.post(
    "/jobs/{job_id}/cancel",
    response_model=Cancellation,
    responses={404: {"model": Failure}, **WRITE_ERRORS},
)
def cancel(job_id: UUID, body: Empty, request: Request):
    runtime = request.app.state.runtime
    runtime.jobs.cancel(job_id, request.state.actor)
    runtime.wake.set()
    return {"jobId": job_id, "status": "CANCELLED"}


@router.get(
    "/jobs/{job_id}",
    responses={404: {"model": Failure}},
    response_model=Job,
    response_model_exclude_unset=True,
)
def detail(job_id: UUID, request: Request):
    with job_storage(request.app.state.runtime, job_id).engine.connect() as conn:
        row = query(conn, "SELECT * FROM jobs WHERE id=:id", id=job_id).first()
    if not row:
        raise DomainError("JOB_NOT_FOUND", "Задача не найдена", 404)
    return job_record(row)


@router.get("/jobs/{job_id}/provenance", response_model=Provenance)
def provenance(job_id: UUID, request: Request):
    with job_storage(request.app.state.runtime, job_id).engine.connect() as conn:
        job = query(conn, "SELECT * FROM jobs WHERE id=:id", id=job_id).first()
        if job is None:
            raise DomainError("JOB_NOT_FOUND", "Задача не найдена", 404)
        inputs = (job.result or {}).get("inputVersions", [])
        sources = [UUID(i["sourceJobId"]) for i in inputs if i["sourceJobId"]]
        source_jobs = (
            {str(row.id): row for row in query(conn, "SELECT * FROM jobs WHERE id=ANY(:ids)", ids=sources)}
            if sources
            else {}
        )

    revisions, expected, lineage = set(), 0, []
    for item in inputs:
        source = source_jobs.get(item["sourceJobId"])
        factors = None
        if source and (source.handler or source.process) in (
            "credit-risk",
            "market-risk",
        ):
            expected += 1
            factor_key = source.bindings.get("risk_parameters", "risk_parameters")
            factors = next(
                (
                    version
                    for version in (source.result or {}).get("inputVersions", [])
                    if version["resourceKey"] == factor_key
                ),
                None,
            )
            if factors:
                revisions.add(
                    (
                        factors["resourceKey"],
                        factors["revision"],
                        factors["sourceJobId"],
                    )
                )
        lineage.append(
            {
                **item,
                "sourceProcessId": source.process if source else None,
                "coefficients": factors,
            }
        )

    status = "NOT_APPLICABLE"
    if (job.handler or job.process) == "risk-summary":
        known = sum(item["coefficients"] is not None for item in lineage)
        if not expected or known < expected:
            status = "UNAVAILABLE"
        else:
            status = "MIXED_REVISIONS" if len(revisions) > 1 else "SAME_REVISION"
    return {"inputs": lineage, "coefficientStatus": status}


@router.get("/jobs", response_model=JobPage, response_model_exclude_unset=True)
def jobs(
    request: Request,
    view: Literal["queue", "active", "history"] = "queue",
    status: Status | None = None,
    processId: str | None = None,
    calculationDate: date | None = None,
    triggerType: Source | None = None,
    cursor: str | None = None,
    limit: PageSize = 50,
):
    states = {
        "queue": ["QUEUED", "WAITING"],
        "active": ["STARTING", "RUNNING"],
        "history": ["SUCCEEDED", "FAILED", "CANCELLED"],
    }
    filters = [view, status, processId, str(calculationDate), triggerType]
    conditions = ["status=ANY(:states)"]
    params = {"states": states[view], "limit": limit + 1}
    for field, value in (
        ("status", status),
        ("process", processId),
        ("day", calculationDate),
        ("trigger->>'type'", triggerType),
    ):
        if value is not None:
            name = f"filter{len(conditions)}"
            conditions.append(f"{field}=:{name}")
            params[name] = value
    reverse = view == "history"
    if cursor:
        try:
            saved, seq = json.loads(base64.urlsafe_b64decode(cursor))
            if saved != filters or type(seq) is not int or not 0 < seq < 2**63:
                raise ValueError
        except (ValueError, TypeError):
            raise DomainError("INVALID_CURSOR", "Курсор не соответствует запросу", 422) from None
        conditions.append("seq < :seq" if reverse else "seq > :seq")
        params["seq"] = seq
    order = "DESC" if reverse else "ASC"
    with request.app.state.runtime.storage.engine.connect() as conn:
        rows = query(
            conn,
            f"SELECT * FROM jobs WHERE {' AND '.join(conditions)} ORDER BY seq {order} LIMIT :limit",
            **params,
        ).all()
    continuation = None
    if len(rows) > limit:
        continuation = base64.urlsafe_b64encode(encode([filters, rows[limit - 1].seq]).encode()).decode()
    return {
        "items": [job_record(row) for row in rows[:limit]],
        "nextCursor": continuation,
    }


@router.get("/events", response_model=EventPage)
def events(
    request: Request,
    afterEventId: Annotated[int, Query(ge=0, lt=2**63)] = 0,
    jobId: UUID | None = None,
    limit: PageSize = 50,
):
    predicate = "AND job_id=:job" if jobId else ""
    with job_storage(request.app.state.runtime, jobId).engine.connect() as conn:
        rows = query(
            conn,
            f"SELECT * FROM job_events WHERE id>:after {predicate} ORDER BY id LIMIT :limit",
            after=afterEventId,
            job=jobId,
            limit=limit,
        ).all()
    return {
        "items": [
            {
                "eventId": r.id,
                "jobId": r.job_id,
                "type": r.type,
                "status": r.status,
                "occurredAt": r.occurred,
                "payload": r.payload,
            }
            for r in rows
        ],
        "nextAfterEventId": rows[-1].id if rows else afterEventId,
    }


@router.get("/overview", response_model=Overview)
def overview(request: Request):
    runtime = request.app.state.runtime
    counts = dict.fromkeys(get_args(Status), 0)
    waits = {"RESOURCE_CONFLICT": 0, "NO_WORKER_SLOT": 0}
    with runtime.storage.engine.connect() as conn:
        for row in query(
            conn,
            "SELECT status,reason->>'code' AS reason,count(*) AS n FROM jobs GROUP BY 1,2",
        ):
            counts[row.status] += row.n
            if row.reason:
                waits[row.reason] = waits.get(row.reason, 0) + row.n
    return {
        "serverTime": timestamp(datetime.now(UTC)),
        "countsByStatus": counts,
        "occupiedSlots": counts["STARTING"] + counts["RUNNING"],
        "maxSlots": runtime.jobs.slots,
        "resourceWaitingCount": waits["RESOURCE_CONFLICT"],
        "slotWaitingCount": waits["NO_WORKER_SLOT"],
    }
