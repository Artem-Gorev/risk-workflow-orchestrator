from uuid import uuid4

from backend.workers.synthetic import case_expectations, cases_for, configured_period

from .snapshots import freeze
from .storage import DomainError, encode, query

RISKS = ("credit-risk", "market-risk", "operational-risk", "liquidity-risk")
SUMMARY = "risk-summary"


def add_step(service, conn, batch, code):
    job, _ = service.submit(
        conn,
        code,
        batch.day,
        {"type": "MANUAL", "initiatorId": batch.actor, "batchId": str(batch.id)},
    )
    if job.handler != code or job.bindings:
        raise DomainError(
            "BATCH_CONTRACT_CHANGED",
            f"Для шага {code} нужен стандартный контракт ресурсов",
        )
    query(conn, "UPDATE jobs SET batch_id=:b WHERE id=:j", b=batch.id, j=job.id)
    return job


def create_batch(runtime, day, revision, actor):
    case = next((c for c in cases_for(*configured_period()) if c.day == day), None)
    if case is None:
        start, end = configured_period()
        raise DomainError("DATE_OUTSIDE_SYNTHETIC_RANGE", f"Допустимый диапазон {start} – {end}", 422)
    with runtime.storage.transaction() as conn:
        factors = query(conn, "SELECT value FROM factor_history WHERE revision=:r", r=revision).first()
        if not factors:
            raise DomainError("UNKNOWN_REVISION", "Неизвестная ревизия коэффициентов", 422)
        expected, _ = case_expectations(case.inputs, factors.value)
        batch = query(
            conn,
            """INSERT INTO risk_batches(id,day,factor_revision,expected,actor)
            VALUES (:id,:d,:r,CAST(:e AS jsonb),:a) RETURNING *""",
            id=uuid4(),
            d=day,
            r=revision,
            e=encode(expected),
            a=actor,
        ).one()
        try:
            with conn.begin_nested():
                for code in RISKS:
                    add_step(runtime.jobs, conn, batch, code)
        except DomainError as error:
            query(
                conn,
                "UPDATE risk_batches SET status='FAILED',error=:error WHERE id=:id",
                error=f"{code} · {error}",
                id=batch.id,
            )
    runtime.wake.set()
    return {"batchId": batch.id}


def advance(service):
    with service.storage.transaction() as conn:
        for batch in query(conn, "SELECT * FROM risk_batches WHERE status='RUNNING' ORDER BY created").all():
            rows = query(conn, "SELECT * FROM jobs WHERE batch_id=:b ORDER BY seq", b=batch.id).all()
            failed = next((j for j in rows if j.status in ("FAILED", "CANCELLED")), None)
            if failed:
                status, reason = (
                    "FAILED",
                    f"{failed.process} · {(failed.error or {}).get('message', failed.status)}",
                )
            elif any(j.status != "SUCCEEDED" for j in rows):
                continue
            elif any(j.process == SUMMARY for j in rows):
                status, reason = "SUCCEEDED", None
            else:
                try:
                    with conn.begin_nested():
                        summary = add_step(service, conn, batch, SUMMARY)
                        inputs, versions = {}, []
                        for j in rows:
                            if j.process == "operational-risk":
                                continue
                            (out,) = j.result["outputs"]
                            inputs[out["resourceKey"]] = out["value"]
                            versions.append(
                                {k: v for k, v in out.items() if k != "value"} | {"sourceJobId": str(j.id)}
                            )
                        freeze(conn, summary, inputs, versions)
                    continue
                except DomainError as exc:
                    status, reason = "FAILED", f"{SUMMARY} · {exc}"
            query(
                conn,
                "UPDATE risk_batches SET status=:s,error=:e WHERE id=:id",
                s=status,
                e=reason,
                id=batch.id,
            )


def batch_record(conn, batch_id):
    batch = query(conn, "SELECT * FROM risk_batches WHERE id=:id", id=batch_id).first()
    if not batch:
        raise DomainError("BATCH_NOT_FOUND", "Серия расчётов не найдена", 404)
    rows = {j.process: j for j in query(conn, "SELECT * FROM jobs WHERE batch_id=:id", id=batch_id)}
    steps = []
    for code in (*RISKS, SUMMARY):
        j = rows.get(code)
        actual = j.result["outputs"][0]["value"] if j and j.result else None
        steps.append(
            {
                "processId": code,
                "jobId": j.id if j else None,
                "status": j.status if j else "PENDING",
                "error": j.error if j else None,
                "expected": batch.expected[code],
                "actual": actual,
                "passed": actual == batch.expected[code] if actual is not None else None,
            }
        )
    return {
        "batchId": batch.id,
        "calculationDate": batch.day,
        "factorRevision": batch.factor_revision,
        "status": batch.status,
        "error": batch.error,
        "steps": steps,
    }
