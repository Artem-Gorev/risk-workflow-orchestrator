import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime, timedelta

import pytest
from alembic import command
from alembic.config import Config

from backend.analytics.report import report
from backend.orchestrator.resources import conflicts
from backend.orchestrator.service import JobService
from backend.orchestrator.snapshots import execution_inputs
from backend.orchestrator.storage import Storage, load_inputs, query
from backend.scheduler.service import tick
from backend.workers.catalog import PROCESSES

DAY = date(2026, 11, 26)


@pytest.fixture
def service(db):
    command.upgrade(Config("alembic.ini"), "head")
    store = Storage(os.environ["DATABASE_URL"])
    store.seed()
    yield JobService(store, 2)
    store.engine.dispose()


def enqueue(service, process="credit-risk", day=DAY):
    with service.storage.transaction() as conn:
        job, _ = service.submit(conn, process, day, {"type": "MANUAL", "initiatorId": "analyst"})
        return job


@pytest.mark.parametrize("refresh, error", [(True, "STALE_INPUTS"), (False, "STALE_OUTPUT")])
def test_stale_retry_keeps_current_output(service, refresh, error):
    def start():
        job = service.reserve()
        assert service.start(job)
        with service.storage.transaction() as conn:
            active = query(conn, "SELECT * FROM jobs WHERE id=:id", id=job.id).one()
            values, versions = execution_inputs(conn, active)
        return job, PROCESSES[job.handler].calculate(values), versions

    enqueue(service)
    first, _, _ = start()
    with service.storage.transaction() as conn:
        service.fail(conn, first, "WORKER_LOST", "Тестовый обрыв")
        query(conn, "UPDATE jobs SET retry_at=now()+interval '1 hour' WHERE id=:id", id=first.id)
    if refresh:
        enqueue(service, "refresh-parameters", DAY + timedelta(days=1))
        service.complete(*start())
    enqueue(service)
    newer = start()
    service.complete(*newer)
    with service.storage.transaction() as conn:
        query(conn, "UPDATE jobs SET retry_at=NULL WHERE id=:id", id=first.id)
    retry = start()
    assert retry[0].id == first.id and retry[0].attempt == 2
    service.complete(*retry)

    with service.storage.engine.connect() as conn:
        published = query(conn, "SELECT value,source FROM resource_values WHERE key='credit_risk'").one()
        assert published == (newer[1]["credit_risk"], newer[0].id)
        status, failure = query(conn, "SELECT status,error FROM jobs WHERE id=:id", id=first.id).one()
        assert status == "FAILED" and failure["code"] == error


def test_worker_load_keeps_interrupted_attempts(service):
    enqueue(service)
    attempt = service.reserve()
    assert service.start(attempt)
    with service.storage.transaction() as conn:
        query(
            conn,
            "UPDATE job_events SET occurred=clock_timestamp()-interval '30 seconds' "
            "WHERE job_id=:id AND type='JOB_STARTED'",
            id=attempt.id,
        )
    with service.storage.engine.connect() as conn:
        before = sum(bucket["busySeconds"] for bucket in report(conn)["load"])
        assert before >= 30
    with service.storage.transaction() as conn:
        service.fail(conn, attempt, "WORKER_LOST", "Тестовый обрыв")
        query(conn, "UPDATE jobs SET retry_at=NULL WHERE id=:id", id=attempt.id)
    with service.storage.engine.connect() as conn:
        assert sum(bucket["busySeconds"] for bucket in report(conn)["load"]) >= before

    retry = service.reserve()
    assert service.start(retry)
    with service.storage.engine.connect() as conn:
        versions = load_inputs(conn, retry)[1]
    service.complete(retry, {"credit_risk": "10.00"}, versions)
    with service.storage.engine.connect() as conn:
        assert sum(bucket["busySeconds"] for bucket in report(conn)["load"]) >= before


def reason(service, job_id):
    with service.storage.engine.connect() as conn:
        return query(conn, "SELECT reason FROM jobs WHERE id=:id", id=job_id).scalar_one()


def test_scopes_and_atomic_sets(service, monkeypatch):
    from backend.workers.catalog import PROCESSES, Process

    day = {"type": "DATE", "date": str(DAY)}
    access = {"resourceKey": "x", "scope": day, "mode": "READ"}
    owner = {"resourceKey": "x", "scope": {"type": "GLOBAL"}, "mode": "WRITE", "jobId": "active"}
    assert conflicts([access], [owner])[0]["scope"] == day
    assert [
        conflict["blockingJobId"]
        for conflict in conflicts([access, access], [owner, owner, dict(owner, jobId="another")])
    ] == ["active", "another"]
    assert not conflicts([access], [dict(owner, mode="READ")])
    assert not conflicts([access], [dict(owner, resourceKey="y")])
    assert not conflicts([access], [dict(owner, scope={"type": "DATE", "date": "2026-11-27"})])
    for code, source, target in [("xy", "x", "y"), ("yx", "y", "x")]:
        monkeypatch.setitem(
            PROCESSES, code, Process(code, code, (), (source, target), lambda _: {"x": "1.00", "y": "2.00"})
        )
        service.storage.seed()
        enqueue(service, code)
    with ThreadPoolExecutor(2) as pool:
        results = list(pool.map(lambda _: service.reserve(), range(2)))
    assert sum(r is not None for r in results) == 1
    with service.storage.engine.connect() as conn:
        assert query(conn, "SELECT count(*), count(DISTINCT job_id) FROM reservations").one() == (2, 1)


def test_wait_reason_change_and_slot(service):
    service.slots = 1
    a = enqueue(service)
    running = service.reserve()
    assert running.id == a.id
    assert service.start(running)
    b = enqueue(service, "liquidity-risk")
    c = enqueue(service)
    assert service.reserve() is None
    assert reason(service, b.id)["code"] == "NO_WORKER_SLOT"
    assert reason(service, c.id)["code"] == "RESOURCE_CONFLICT"
    with service.storage.transaction() as conn:
        service.fail(conn, running, "EXECUTION_ERROR", "Контрольный отказ")
    assert service.reserve().id == b.id
    assert service.reserve() is None
    assert reason(service, c.id)["code"] == "NO_WORKER_SLOT"
    with service.storage.engine.connect() as conn:
        assert (
            query(conn, "SELECT count(*) FROM job_events WHERE type='JOB_WAIT_REASON_CHANGED'").scalar_one()
            == 1
        )


def test_global_writer(service):
    enqueue(service, "refresh-parameters", DAY + timedelta(days=1))
    writer = service.reserve()
    assert service.start(writer)
    reader = enqueue(service)
    assert service.reserve() is None
    assert reason(service, reader.id)["conflicts"][0]["scope"] == {"type": "GLOBAL"}
    with service.storage.engine.connect() as conn:
        versions = load_inputs(conn, writer)[1]
    service.complete(writer, {"risk_parameters": {"creditFactor": "0.15", "marketFactor": "0.25"}}, versions)
    assert service.reserve().id == reader.id


def test_publication_rollback_and_replayed_callbacks(service):
    enqueue(service)
    job = service.reserve()
    assert service.start(job)
    assert not service.start(job)
    with service.storage.engine.connect() as conn:
        inputs = load_inputs(conn, job)[1]
    output = {"credit_risk": "10.00"}
    service.complete(job, output, inputs)
    service.complete(job, output, inputs)
    with service.storage.transaction() as conn:
        service.fail(conn, job, "RESULT_WRITE_ERROR", "Потерян ответ commit")
    enqueue(service)
    other = service.reserve()
    service.start(other)
    from unittest.mock import patch

    with patch.object(service, "event", side_effect=RuntimeError("rollback")), pytest.raises(RuntimeError):
        service.complete(other, {"credit_risk": "15.00"}, inputs)
    with service.storage.transaction() as conn:
        service.fail(conn, other, "RESULT_WRITE_ERROR", "Откат транзакции")
        assert query(conn, "SELECT revision,value FROM resource_values WHERE key='credit_risk'").one() == (
            1,
            "10.00",
        )
        assert query(conn, "SELECT count(*) FROM job_events WHERE type='JOB_SUCCEEDED'").scalar_one() == 1
        assert query(conn, "SELECT count(*) FROM reservations").scalar_one() == 0


def test_daily_and_missed_delivery(service):
    now = datetime(2026, 11, 25, 23, 3, tzinfo=UTC)
    with service.storage.transaction() as conn:
        query(
            conn,
            """INSERT INTO schedules(id,process,due,daily) VALUES ('daily','credit-risk',:due,true),
                       ('expired','market-risk',:expired,false)""",
            due=now - timedelta(minutes=3),
            expired=now - timedelta(minutes=6),
        )
    tick(service, now)
    tick(service, now)
    with service.storage.engine.connect() as conn:
        job = query(conn, "SELECT day, trigger FROM jobs").one()
        assert job.day == DAY
        assert job.trigger["scheduledFor"] == "2026-11-25T23:00:00Z"
        assert query(conn, "SELECT due FROM schedules WHERE id='daily'").scalar_one() == datetime(
            2026, 11, 26, 23, tzinfo=UTC
        )


def test_invalid_output(service):
    enqueue(service)
    job = service.reserve()
    service.start(job)
    service.complete(job, {"foreign": "99.00"}, [])
    with service.storage.engine.connect() as conn:
        assert query(conn, "SELECT error->>'code' FROM jobs").scalar_one() == "INVALID_OUTPUT"
        assert query(conn, "SELECT count(*) FROM reservations").scalar_one() == 0


def test_priority_aging_protects_waiting_writer(service):
    enqueue(service, "credit-risk")
    reader = service.reserve()
    assert service.start(reader)
    with service.storage.transaction() as conn:
        writer, _ = service.submit(
            conn, "refresh-parameters", DAY, {"type": "MANUAL", "initiatorId": "a"}, priority=9
        )
        query(conn, "UPDATE jobs SET queued=now()-interval '5 minutes' WHERE id=:id", id=writer.id)
        late, _ = service.submit(conn, "market-risk", DAY, {"type": "MANUAL", "initiatorId": "b"}, priority=1)
    assert service.reserve() is None
    assert reason(service, writer.id)["code"] == "RESOURCE_CONFLICT"
    assert reason(service, late.id)["code"] == "QUEUE_FAIRNESS"
    with service.storage.transaction() as conn:
        service.fail(conn, reader, "EXECUTION_ERROR", "Контрольное завершение")
    assert service.reserve().id == writer.id


def test_priority_and_weekly_scheduler(service):
    low = enqueue(service)
    with service.storage.transaction() as conn:
        high, _ = service.submit(
            conn, "liquidity-risk", DAY, {"type": "MANUAL", "initiatorId": "b"}, priority=1
        )
    assert service.reserve().id == high.id
    assert service.reserve().id == low.id
    monday = datetime(2026, 11, 23, 1, 0, tzinfo=UTC)
    with service.storage.transaction() as conn:
        query(
            conn,
            """INSERT INTO schedules(id,process,due,daily,local_time,weekday)
              VALUES ('monday','operational-risk',:due,true,'04:00',0)""",
            due=monday,
        )
    tick(service, monday + timedelta(minutes=1))
    tick(service, monday + timedelta(minutes=2))
    with service.storage.engine.connect() as conn:
        assert query(conn, "SELECT due FROM schedules WHERE id='monday'").scalar_one() == monday + timedelta(
            days=7
        )
        assert (
            query(conn, "SELECT count(*) FROM jobs WHERE trigger->>'scheduleId'='monday'").scalar_one() == 1
        )


def test_join_active_keeps_manual_and_scheduled_jobs_separate(service):
    now = datetime.now(UTC)
    with service.storage.transaction() as conn:
        query(conn, "UPDATE processes SET join_active=true WHERE process_id='credit-risk'")
        query(
            conn,
            """INSERT INTO schedules(id,process,due,day) VALUES
               ('first','credit-risk',:due,:day),('second','credit-risk',:due,:day)""",
            due=now,
            day=DAY,
        )
    manual = enqueue(service)
    running = service.reserve()
    assert running.id == manual.id
    assert service.start(running)
    assert enqueue(service).id == manual.id
    tick(service, now)
    assert service.reserve() is None
    with service.storage.engine.connect() as conn:
        jobs = query(conn, "SELECT id,status,trigger->>'type' AS source FROM jobs ORDER BY seq").all()
        assert [(j.status, j.source) for j in jobs] == [("RUNNING", "MANUAL"), ("WAITING", "SCHEDULED")]
        assert jobs[1].id != manual.id
        joined = query(conn, "SELECT job_id FROM job_events WHERE type='JOB_TRIGGER_JOINED' ORDER BY id")
        assert [row.job_id for row in joined] == [manual.id, jobs[1].id]
        scheduled = query(conn, "SELECT job_id FROM schedule_history ORDER BY id")
        assert [row.job_id for row in scheduled] == [jobs[1].id, jobs[1].id]
