from uuid import uuid4

from backend.orchestrator.catalog import latest
from backend.orchestrator.storage import DomainError, job_record, query
from backend.workers.synthetic import configured_period


def create_demo(runtime, actor):
    if runtime.jobs.slots < 2:
        raise DomainError("DEMO_REQUIRES_TWO_SLOTS", "Для демонстрации нужны два исполнителя")
    batch_id, scheduled_id, independent_id = uuid4(), uuid4(), uuid4()
    with runtime.storage.transaction() as conn:
        if query(conn, "SELECT 1 FROM jobs WHERE finished IS NULL LIMIT 1").first():
            raise DomainError("DEMO_QUEUE_BUSY", "Дождитесь завершения текущих задач")
        for code in ("credit-risk", "liquidity-risk"):
            definition = latest(conn, code)
            if not definition.active or (definition.handler or code) != code or definition.bindings:
                raise DomainError("DEMO_PROCESS_CHANGED", f"Для демо нужен исходный процесс {code}")
        first, _ = runtime.jobs.submit(
            conn,
            "credit-risk",
            configured_period()[0],
            {"type": "MANUAL", "initiatorId": actor, "demoId": str(batch_id)},
            priority=5,
            duration=max(6, runtime.config.scheduler_poll + 3),
        )
        query(
            conn,
            """INSERT INTO demo_runs(id,first_job,scheduled_job,independent_job)
                VALUES (:id,:first,:scheduled,:independent)""",
            id=batch_id,
            first=first.id,
            scheduled=scheduled_id,
            independent=independent_id,
        )
        query(
            conn,
            """INSERT INTO schedules(id,process,due,day,demo_id)
                VALUES (:schedule,'credit-risk',clock_timestamp(),:day,:id)""",
            schedule=f"demo-{batch_id}",
            day=configured_period()[0],
            id=batch_id,
        )
    runtime.wake.set()
    return {"demoId": batch_id, "jobs": [first.id, scheduled_id, independent_id]}


def demo_status(conn, batch_id):
    batch = query(conn, "SELECT * FROM demo_runs WHERE id=:id", id=batch_id).first()
    if batch is None:
        raise DomainError("DEMO_NOT_FOUND", "Демонстрация не найдена", 404)
    jobs = [batch.first_job, batch.scheduled_job, batch.independent_job]
    items = {
        row.id: job_record(row) for row in query(conn, "SELECT * FROM jobs WHERE id=ANY(:ids)", ids=jobs)
    }
    return {
        "demoId": batch_id,
        "jobs": jobs,
        "items": [items[job_id] for job_id in jobs if job_id in items],
        "scheduleSlot": query(
            conn,
            "SELECT * FROM schedule_history WHERE schedule_id=:id ORDER BY id DESC LIMIT 1",
            id=f"demo-{batch_id}",
        )
        .mappings()
        .first(),
        "status": batch.status,
        "error": batch.error,
    }
