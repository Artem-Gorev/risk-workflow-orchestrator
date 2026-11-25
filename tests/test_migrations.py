import os
from uuid import uuid4

import pytest
from alembic import command
from alembic.config import Config

from backend.orchestrator.service import JobService
from backend.orchestrator.storage import Storage, encode, query


@pytest.mark.parametrize("revision", ["003", "005", "006"])
def test_upgrade_counts_attempts_already_started(db, revision):
    config = Config("alembic.ini")
    command.upgrade(config, revision)
    store = Storage(os.environ["DATABASE_URL"])
    job = uuid4()
    try:
        with store.transaction() as conn:
            query(
                conn,
                """INSERT INTO jobs(id,process,day,trigger,accesses,status,execution,started,lease_until)
                   VALUES (:id,'credit-risk','2026-11-26','{"type":"MANUAL","initiatorId":"test"}','[]',
                           'RUNNING',:execution,now(),now()+interval '15 seconds')""",
                id=job,
                execution=uuid4(),
            )
            if revision == "006":
                query(conn, "UPDATE jobs SET attempt=1 WHERE id=:id", id=job)
                query(
                    conn,
                    """INSERT INTO job_events(job_id,type,status,occurred,payload) VALUES
                       (:id,'JOB_RETRY_SCHEDULED','QUEUED',now(),jsonb_build_object('attempt',0)),
                       (:id,'JOB_RESERVED','STARTING',now(),jsonb_build_object('attempt',1))""",
                    id=job,
                )
        command.upgrade(config, "head")
        JobService(store, 2).recover()
        with store.engine.connect() as conn:
            attempt = 2 if revision == "006" else 1
            assert query(conn, "SELECT attempt FROM jobs WHERE id=:id", id=job).scalar_one() == attempt
            retries = query(
                conn,
                "SELECT payload->'attempt' AS attempt FROM job_events WHERE job_id=:id "
                "AND type='JOB_RETRY_SCHEDULED' ORDER BY id",
                id=job,
            ).scalars().all()
            assert retries == list(range(1, attempt + 1))
    finally:
        store.engine.dispose()


def test_upgrade_marks_previously_successful_batch_with_mismatch(db):
    config = Config("alembic.ini")
    command.upgrade(config, "007")
    store = Storage(os.environ["DATABASE_URL"])
    batch_id, job_id = uuid4(), uuid4()
    access = {"resourceKey": "risk_summary", "scope": {"type": "DATE", "date": "2026-11-26"}, "mode": "WRITE"}
    try:
        with store.transaction() as conn:
            query(
                conn,
                """INSERT INTO risk_batches(id,day,factor_revision,expected,actor,status)
                   VALUES (:id,'2026-11-26',1,CAST(:expected AS jsonb),'tester','SUCCEEDED')""",
                id=batch_id, expected=encode({"risk-summary": "50.00"}),
            )
            query(
                conn,
                """INSERT INTO jobs(id,process,day,trigger,accesses,status,batch_id,result)
                   VALUES (:id,'risk-summary','2026-11-26','{}',CAST(:access AS jsonb),
                           'SUCCEEDED',:batch,CAST(:result AS jsonb))""",
                id=job_id, batch=batch_id, access=encode([access]),
                result=encode({"outputs": [access | {"value": "60.00"}]}),
            )
        command.upgrade(config, "head")
        with store.engine.connect() as conn:
            status, error = query(
                conn, "SELECT status,error FROM risk_batches WHERE id=:id", id=batch_id
            ).one()
            assert status == "FAILED" and "risk-summary" in error
            accesses = query(conn, "SELECT accesses FROM jobs WHERE id=:id", id=job_id).scalar_one()
            assert accesses == [access | {"isolated": True}]
    finally:
        store.engine.dispose()
