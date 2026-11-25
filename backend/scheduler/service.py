from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

from backend.orchestrator.storage import DomainError, query, timestamp

MOSCOW = ZoneInfo("Europe/Moscow")


def next_slot(now, local_time, weekday=None):
    local = now.astimezone(MOSCOW)
    due = datetime.combine(local.date(), local_time, MOSCOW)
    if weekday is not None:
        due += timedelta(days=(weekday - due.weekday()) % 7)
    return due if due > local else due + timedelta(days=7 if weekday is not None else 1)


def next_daily(now):
    return next_slot(now, time(2))


def deliver_demo(service, conn, slot, trigger, now):
    demo = query(
        conn,
        """SELECT d.*,j.status AS first_status,j.trigger AS first_trigger
                        FROM demo_runs d JOIN jobs j ON j.id=d.first_job WHERE d.id=:id""",
        id=slot.demo_id,
    ).one()
    if demo.first_status in ("QUEUED", "WAITING", "STARTING"):
        return
    reason: str | None
    try:
        if demo.first_status != "RUNNING":
            raise DomainError(
                "DEMO_RUN_INTERRUPTED",
                "Первый расчёт завершился до доставки регламента",
            )
        with conn.begin_nested():
            trigger["demoId"] = str(slot.demo_id)
            service.submit(
                conn,
                slot.process,
                slot.day,
                trigger,
                trigger["scheduledFor"],
                priority=5,
                job_id=demo.scheduled_job,
                duration=1,
            )
            service.submit(
                conn,
                "liquidity-risk",
                slot.day,
                {
                    "type": "MANUAL",
                    "initiatorId": demo.first_trigger["initiatorId"],
                    "demoId": str(slot.demo_id),
                },
                priority=5,
                job_id=demo.independent_job,
                duration=1,
            )
    except DomainError as error:
        status, reason, job_id = "REJECTED", str(error), None
        query(
            conn,
            "UPDATE demo_runs SET status='FAILED',error=:error WHERE id=:id",
            id=slot.demo_id,
            error=str(error),
        )
    else:
        status, reason, job_id = "DELIVERED", None, demo.scheduled_job
        query(
            conn,
            "UPDATE demo_runs SET status='DELIVERED' WHERE id=:id",
            id=slot.demo_id,
        )
    record_slot(conn, slot.id, slot.due, now, status, reason, job_id)
    query(conn, "UPDATE schedules SET due=NULL WHERE id=:id", id=slot.id)


def record_slot(conn, schedule_id, planned, observed, status, reason, job_id):
    query(
        conn,
        """INSERT INTO schedule_history(schedule_id,planned,observed,status,reason,job_id)
                    VALUES (:id,:due,:now,:s,:r,:j) ON CONFLICT DO NOTHING""",
        id=schedule_id,
        due=planned,
        now=observed,
        s=status,
        r=reason,
        j=job_id,
    )


def tick(service, now):
    with service.storage.transaction() as conn:
        slots = query(
            conn,
            "SELECT * FROM schedules WHERE enabled AND due<=:now ORDER BY due,id",
            now=now,
        ).all()
        for slot in slots:
            deliver_due_slots(service, conn, slot, now, limit=100)


def deliver_due_slots(service, conn, slot, now, *, limit=None):
    due = slot.due
    delivered = 0
    while due is not None and due <= now and (limit is None or delivered < limit):
        trigger = {
            "type": "SCHEDULED",
            "initiatorId": slot.id,
            "scheduleId": slot.id,
            "scheduledFor": timestamp(due),
        }
        if slot.demo_id:
            deliver_demo(service, conn, slot, trigger, now)
            return
        job_id = None
        if now - due > timedelta(minutes=5):
            status, reason = "MISSED", "Истекло окно доставки 5 минут"
        else:
            try:
                job, _ = service.submit(
                    conn,
                    slot.process,
                    slot.day or due.astimezone(MOSCOW).date(),
                    trigger,
                    trigger["scheduledFor"],
                )
                status, reason, job_id = "DELIVERED", None, job.id
            except DomainError as error:
                status, reason = "REJECTED", f"{error.code} · {error}"
        record_slot(conn, slot.id, due, now, status, reason, job_id)
        due = next_slot(due, slot.local_time, slot.weekday) if slot.daily else None
        delivered += 1
    if delivered:
        query(conn, "UPDATE schedules SET due=:due WHERE id=:id", id=slot.id, due=due)
