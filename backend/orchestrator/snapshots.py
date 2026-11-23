from hashlib import sha256
from pathlib import Path

from backend.workers import catalog

from .storage import DomainError, encode, load_inputs, query

HANDLER_VERSION = sha256(Path(catalog.__file__).read_bytes()).hexdigest()


def freeze(conn, job, values, versions):
    snapshot = {
        "handler": job.handler or job.process,
        "handlerVersion": HANDLER_VERSION,
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
        if job.batch_id and "risk_parameters" in values:
            factor = query(
                conn,
                """SELECT f.* FROM risk_batches b JOIN factor_history f
                ON f.revision=b.factor_revision WHERE b.id=:id""",
                id=job.batch_id,
            ).one()
            values["risk_parameters"] = factor.value
            for item in versions:
                if item["resourceKey"] == "risk_parameters":
                    item.update(
                        revision=factor.revision,
                        sourceJobId=str(factor.source) if factor.source else None,
                    )
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
