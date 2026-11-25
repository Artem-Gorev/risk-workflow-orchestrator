from hashlib import sha256
from pathlib import Path

from backend.workers import catalog

from .storage import DomainError, encode, load_inputs, query, timestamp

HANDLER_VERSION = sha256(Path(catalog.__file__).read_bytes()).hexdigest()


def freeze(conn, job, values, versions):
    snapshot = {
        "handler": job.handler or job.process,
        "handlerVersion": HANDLER_VERSION,
        "attempt": job.attempt,
        "startedAt": timestamp(job.started),
        "inputs": values,
        "versions": versions,
    }
    query(
        conn,
        "UPDATE jobs SET snapshot=CAST(:s AS jsonb) WHERE id=:id AND snapshot IS NULL",
        s=encode(snapshot),
        id=job.id,
    )
    return snapshot


def execution_inputs(conn, job):
    snapshot = job.snapshot
    if snapshot is None:
        values, versions = load_inputs(conn, job)
        if (job.handler or job.process) == "risk-summary":
            revisions = set()
            for item in versions:
                if item["resourceKey"] == job.bindings.get("liquidity_risk", "liquidity_risk"):
                    continue
                source = query(
                    conn,
                    "SELECT result,bindings FROM jobs WHERE id=:id",
                    id=item["sourceJobId"],
                ).first()
                factor = (
                    next(
                        (
                            v
                            for v in source.result["inputVersions"]
                            if v["resourceKey"] == source.bindings.get("risk_parameters", "risk_parameters")
                        ),
                        None,
                    )
                    if source and source.result
                    else None
                )
                if not factor:
                    raise DomainError(
                        "REVISION_UNKNOWN",
                        "Не удалось проверить ревизию входной оценки",
                    )
                revisions.add((factor["resourceKey"], factor["revision"]))
            if len(revisions) != 1:
                raise DomainError(
                    "MIXED_REVISIONS",
                    "Кредитная и рыночная оценки используют разные ревизии",
                )
        snapshot = freeze(conn, job, values, versions)
    if snapshot["handlerVersion"] != HANDLER_VERSION:
        raise DomainError("HANDLER_VERSION_UNAVAILABLE", "Версия обработчика из снимка недоступна")
    return snapshot["inputs"], snapshot["versions"]
