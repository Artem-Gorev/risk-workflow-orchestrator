from dataclasses import replace
from math import ceil
from time import monotonic, sleep
from uuid import uuid4

from backend.config import BackendConfig
from backend.orchestrator.isolation import provision
from backend.orchestrator.resources import conflicts
from backend.orchestrator.storage import Storage, encode, query
from backend.runtime import Runtime
from backend.workers.synthetic import configured_period

WORKLOAD = ("credit-risk", "credit-risk", "market-risk", "liquidity-risk") * 3


def measure(conn):
    rows = query(conn, "SELECT * FROM jobs ORDER BY seq").all()
    events = query(conn, "SELECT * FROM job_events ORDER BY id").all()
    active, overlaps = {}, 0
    for event in events:
        if event.type == "JOB_RESERVED":
            accesses = event.payload["reservedAccesses"]
            overlaps += sum(bool(conflicts(accesses, owner)) for owner in active.values())
            active[event.job_id] = [dict(a, jobId=str(event.job_id)) for a in accesses]
        if "releasedAccesses" in event.payload:
            active.pop(event.job_id, None)
    if active or any(j.status != "SUCCEEDED" for j in rows):
        raise RuntimeError("Неполный эксперимент")
    waits = sorted((j.started - j.queued).total_seconds() for j in rows)
    return {
        "makespanSeconds": (max(j.finished for j in rows) - min(j.queued for j in rows)).total_seconds(),
        "p95WaitSeconds": waits[ceil(0.95 * len(waits)) - 1],
        "conflictingPairs": overlaps,
        "jobs": [
            {
                "id": str(j.id),
                "process": j.process,
                "queued": j.queued.isoformat(),
                "started": j.started.isoformat(),
                "finished": j.finished.isoformat(),
            }
            for j in rows
        ],
        "events": [
            {
                "id": e.id,
                "job": str(e.job_id),
                "type": e.type,
                "at": e.occurred.isoformat(),
                "payload": e.payload,
            }
            for e in events
            if e.type in ("JOB_RESERVED", "JOB_SUCCEEDED")
        ],
    }


def run_policy(config, store, policy):
    schema = "risk_bench_" + uuid4().hex
    url = provision(store, schema)
    runtime = Runtime(
        replace(
            config,
            database_url=url,
            worker_slots=2,
            daily_enabled=False,
            worker_delay=0.20,
            scheduler_poll=0.05,
            dispatcher_poll=0.02,
        )
    )
    runtime.jobs.fifo = policy == "fifo"
    try:
        runtime.open()
        try:
            with runtime.storage.transaction() as conn:
                for code in WORKLOAD:
                    runtime.jobs.submit(
                        conn,
                        code,
                        configured_period()[0],
                        {"type": "MANUAL", "initiatorId": "benchmark"},
                    )
            runtime.wake.set()
            deadline = monotonic() + 60
            while monotonic() < deadline:
                with runtime.storage.engine.connect() as conn:
                    if query(conn, "SELECT bool_and(finished IS NOT NULL) FROM jobs").scalar_one():
                        return measure(conn)
                sleep(0.05)
            raise TimeoutError("Истекло время эксперимента")
        finally:
            runtime.close()
    finally:
        with store.engine.begin() as conn:
            query(conn, f'DROP SCHEMA "{schema}" CASCADE')


def main():
    config = BackendConfig.from_env()
    store = Storage(config.database_url)
    try:
        pairs = []
        for n in range(3):
            policies = ("fifo", "riskflow") if n % 2 == 0 else ("riskflow", "fifo")
            pairs.append({p: run_policy(config, store, p) for p in policies})
        report = {"slots": 2, "delaySeconds": 0.2, "workload": WORKLOAD, "pairs": pairs}
        with store.transaction() as conn:
            run_id = uuid4()
            query(
                conn,
                "INSERT INTO benchmark_results(id,report) VALUES (:id,CAST(:r AS jsonb))",
                id=run_id,
                r=encode(report),
            )
        print(run_id)
        for pair in pairs:
            print(
                {
                    p: {k: v for k, v in stats.items() if k not in ("jobs", "events")}
                    for p, stats in pair.items()
                }
            )
    finally:
        store.engine.dispose()


if __name__ == "__main__":
    main()
