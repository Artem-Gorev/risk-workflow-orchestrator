from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, date, datetime
from threading import Event
from time import monotonic, sleep

from fastapi.testclient import TestClient

from backend.app import app
from backend.orchestrator.storage import query
from backend.scheduler.service import tick

D1, D2 = "2026-11-26", "2026-11-27"


def submit(client, process="credit-risk", day=D1, key=None):
    headers = {"Idempotency-Key": key} if key else {}
    return client.post("/api/v1/jobs", json={"processId": process, "calculationDate": day}, headers=headers)


def wait(client, job, status):
    deadline = monotonic() + 8
    while monotonic() < deadline:
        row = client.get(f"/api/v1/jobs/{job}").json()
        if row["status"] == status:
            return row
        sleep(0.02)
    raise AssertionError(row)


def test_queue_and_scheduler(db, monkeypatch):
    gate = Event()
    with TestClient(app) as client:
        compute = app.state.runtime.worker.compute
        original = compute.calculate

        def held(handler, *args):
            if handler == "credit-risk":
                assert gate.wait(8)
            return original(handler, *args)

        monkeypatch.setattr(compute, "calculate", held)
        try:
            first = submit(client).json()["jobId"]
            wait(client, first, "RUNNING")
            now = datetime.now(UTC)
            runtime = app.state.runtime
            with runtime.storage.transaction() as conn:
                query(
                    conn,
                    """INSERT INTO schedules(id,process,due,day) VALUES ('demo','credit-risk',:now,:day)""",
                    now=now,
                    day=date.fromisoformat(D1),
                )
            tick(runtime.jobs, now)
            tick(runtime.jobs, now)
            queued = client.get("/api/v1/jobs", params={"triggerType": "SCHEDULED"}).json()["items"]
            assert len(queued) == 1
            second = queued[0]["jobId"]
            reason = wait(client, second, "WAITING")["waitReason"]
            assert reason["conflicts"][0]["blockingJobId"] == first
            third = submit(client, "liquidity-risk").json()["jobId"]
            assert wait(client, third, "SUCCEEDED")["result"]["outputs"][0]["value"] == "30.00"
            cancelled = submit(client).json()["jobId"]
            assert client.post(f"/api/v1/jobs/{cancelled}/cancel", json={}).json()["status"] == "CANCELLED"
            assert client.post(f"/api/v1/jobs/{first}/cancel", json={}).status_code == 409
        finally:
            gate.set()
        wait(client, second, "SUCCEEDED")
        events = client.get("/api/v1/events").json()["items"]
        ids = {(e["jobId"], e["type"]): e["eventId"] for e in events}
        assert (
            ids[second, "JOB_WAITING"]
            < ids[third, "JOB_STARTED"]
            < ids[first, "JOB_SUCCEEDED"]
            < ids[second, "JOB_RESERVED"]
        )


def test_results_and_revisions(db):
    with TestClient(app) as client:
        for day, totals in [
            (D1, ["10.00", "10.00", "30.00", "50.00"]),
            (D2, ["15.00", "12.00", "30.00", "57.00"]),
        ]:
            for process, expected in zip(
                ("credit-risk", "market-risk", "liquidity-risk", "risk-summary"), totals
            ):
                job = submit(client, process, day).json()["jobId"]
                assert wait(client, job, "SUCCEEDED")["result"]["outputs"][0]["value"] == expected
        refresh = submit(client, "refresh-parameters", D2).json()["jobId"]
        wait(client, refresh, "SUCCEEDED")
        rerun = wait(client, submit(client).json()["jobId"], "SUCCEEDED")
        assert rerun["result"]["outputs"][0]["revision"] == 2
        assert rerun["result"]["outputs"][0]["value"] == "15.00"
        assert any(v["sourceJobId"] == refresh for v in rerun["result"]["inputVersions"])

        mixed_job = submit(client, "risk-summary", D1).json()["jobId"]
        assert wait(client, mixed_job, "FAILED")["error"]["code"] == "MIXED_REVISIONS"


def test_concurrent_delivery_and_paging(db):
    with TestClient(app) as client:
        with ThreadPoolExecutor(20) as pool:
            duplicates = list(pool.map(lambda _: submit(client, key="same"), range(20)))
            batch = list(pool.map(lambda i: submit(client, day=D1 if i % 2 else D2), range(20)))
        assert sum(r.status_code == 201 for r in duplicates) == 1
        assert len({r.json()["jobId"] for r in duplicates}) == 1
        assert submit(client, day=D2, key="same").status_code == 409
        assert len({r.json()["jobId"] for r in batch}) == 20
        for r in batch:
            wait(client, r.json()["jobId"], "SUCCEEDED")
        history = client.get("/api/v1/jobs?view=history&limit=2").json()
        page = client.get("/api/v1/jobs", params={"cursor": history["nextCursor"], "view": "history"}).json()
        assert history["items"][-1]["queueSequence"] > page["items"][0]["queueSequence"]
        timeline = []
        cursor = 0
        while True:
            page = client.get("/api/v1/events", params={"afterEventId": cursor, "limit": 100}).json()
            if not page["items"]:
                break
            timeline.extend(page["items"])
            cursor = page["nextAfterEventId"]
        writers = {}
        for event in timeline:
            job = event["jobId"]
            if event["type"] == "JOB_RESERVED":
                key = next(
                    a["scope"]["date"] for a in event["payload"]["reservedAccesses"] if a["mode"] == "WRITE"
                )
                assert key not in writers
                writers[key] = job
            if event["type"] == "JOB_SUCCEEDED":
                writers = {k: v for k, v in writers.items() if v != job}
        assert not writers


def test_errors_and_restart(db):
    with TestClient(app) as client:
        assert client.get("/health/ready").status_code == 200
        assert submit(client, day="2026-02-30").status_code == 422
        assert submit(client, process="undefined").status_code == 422
        assert client.get("/api/v1/events").json()["items"] == []
        failed = submit(client, "risk-summary").json()["jobId"]
        assert wait(client, failed, "FAILED")["error"]["code"] == "INPUT_NOT_FOUND"
        runtime = app.state.runtime
        runtime.stop.set()
        runtime.wake.set()
        runtime.thread.join()
        interrupted = submit(client).json()["jobId"]
        runtime.jobs.reserve()
        pending = submit(client).json()["jobId"]
    with TestClient(app) as client:
        assert wait(client, interrupted, "SUCCEEDED")["attempt"] == 2
        assert wait(client, pending, "SUCCEEDED")["result"]["outputs"][0]["value"] == "10.00"


def test_proxy_identity_and_event_cursor(db, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "proxy")
    monkeypatch.setenv("PUBLIC_ORIGIN", "https://risk.example")
    with TestClient(app) as client:
        assert client.get("/api/v1/overview").status_code == 401
        client.headers.update({"X-Authenticated-User": "analyst"})
        client.headers["Origin"] = "https://foreign.example"
        assert submit(client).status_code == 403
        client.headers["Origin"] = "https://risk.example"
        job = submit(client).json()["jobId"]
        assert wait(client, job, "SUCCEEDED")["trigger"]["initiatorId"] == "analyst"
        cursor = 0
        received = []
        while True:
            page = client.get("/api/v1/events", params={"afterEventId": cursor, "limit": 1}).json()
            if not page["items"]:
                assert page["nextAfterEventId"] == cursor
                break
            cursor = page["nextAfterEventId"]
            received.extend(page["items"])
        assert received == client.get("/api/v1/events").json()["items"]
        assert client.get("/api/v1/overview").json()["occupiedSlots"] == 0
        assert client.get("/api/v1/jobs?cursor=bad").status_code == 422
