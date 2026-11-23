import json
from contextlib import contextmanager
from datetime import UTC

from sqlalchemy import create_engine, text

from backend.workers.catalog import PROCESSES
from backend.workers.synthetic import cases_for, configured_period


def query(connection, statement, **params):
    return connection.execute(text(statement), params)


def encode(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def timestamp(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z") if value else None


class Storage:
    def __init__(self, url):
        self.engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 4})

    @contextmanager
    def transaction(self):
        with self.engine.begin() as connection:
            query(connection, "SELECT id FROM orchestrator_state WHERE id=1 FOR UPDATE").scalar_one()
            yield connection

    def seed(self):
        rows = [("risk_parameters", "*", {"creditFactor": "0.10", "marketFactor": "0.20"})]
        rows.extend(
            (key, case.day.isoformat(), value)
            for case in cases_for(*configured_period())
            for key, value in case.inputs.items()
        )
        with self.transaction() as conn:
            for process in PROCESSES.values():
                templates = process.templates()
                for template in templates:
                    partitioning = "GLOBAL" if template["scope"] == "GLOBAL" else "BY_DATE"
                    query(
                        conn,
                        """INSERT INTO resources(resource_key,partitioning) VALUES (:key,:partitioning) ON
                            CONFLICT DO NOTHING""",
                        key=template["resourceKey"],
                        partitioning=partitioning,
                    )
                query(
                    conn,
                    """INSERT INTO processes(process_id,version,name,access_templates) VALUES (:process,1,
                        :name,CAST(:templates AS jsonb))
                               ON CONFLICT DO NOTHING""",
                    process=process.code,
                    name=process.name,
                    templates=encode(templates),
                )
                saved = query(
                    conn,
                    "SELECT access_templates FROM processes WHERE process_id=:process AND version=1",
                    process=process.code,
                ).scalar_one()
                if saved != templates:
                    raise RuntimeError(f"Изменился контракт существующей версии {process.code}")
            conn.execute(
                text("""INSERT INTO resource_values(key,partition,value) VALUES (:k,:d,CAST(:v AS jsonb))
                        ON CONFLICT DO NOTHING"""),
                [{"k": key, "d": day, "v": encode(value)} for key, day, value in rows],
            )


class DomainError(Exception):
    def __init__(self, code, message, status=409):
        super().__init__(message)
        self.code, self.status = code, status


def job_record(row):
    return {
        "jobId": str(row.id),
        "attempt": row.attempt,
        "retryAt": timestamp(row.retry_at),
        "hasSnapshot": bool(row.snapshot),
        "batchId": row.batch_id,
        "replayOf": row.replay_of,
        "processId": row.process,
        "calculationDate": str(row.day),
        "processVersion": row.version,
        "trigger": row.trigger,
        "queueSequence": row.seq,
        "priority": row.priority,
        "leaseUntil": timestamp(row.lease_until),
        "status": row.status,
        "accesses": row.accesses,
        "queuedAt": timestamp(row.queued),
        "startedAt": timestamp(row.started),
        "finishedAt": timestamp(row.finished),
        "waitReason": row.reason,
        "result": row.result,
        "error": row.error,
    }


def load_inputs(conn, job):
    inputs, versions = {}, []
    for access in job.accesses:
        key, scope = access["resourceKey"], access["scope"]
        logical_key = next(
            (logical for logical, physical in job.bindings.items() if physical == key),
            key,
        )
        if logical_key not in PROCESSES[job.handler or job.process].inputs:
            continue
        row = query(
            conn,
            "SELECT value,revision,source FROM resource_values WHERE key=:key AND partition=:day",
            key=key,
            day=scope.get("date", "*"),
        ).first()
        if row is None:
            raise DomainError("INPUT_NOT_FOUND", f"Отсутствуют данные {key} для области {scope}")
        inputs[logical_key] = row.value
        versions.append(
            {
                "resourceKey": key,
                "scope": scope,
                "revision": row.revision,
                "sourceJobId": str(row.source) if row.source else None,
            }
        )
    return inputs, versions
