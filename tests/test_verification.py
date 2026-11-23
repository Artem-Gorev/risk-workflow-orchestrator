import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, timedelta
from pathlib import Path
from time import monotonic, sleep

from fastapi.testclient import TestClient

from backend.app import app
from backend.orchestrator.storage import query
from backend.scheduler.service import tick


def finished(client, path):
    deadline = monotonic() + 12
    while monotonic() < deadline:
        response = client.get("/api/v1/" + path)
        response.raise_for_status()
        value = response.json()
        if value["status"] in ("FAILED", "SUCCEEDED"):
            return value
        sleep(0.03)
    raise AssertionError(value)


def test_batch_revision_replay_and_failed_dependency(db):
    with TestClient(app) as c:
        day = "2026-11-23"

        def submit(code, d):
            r = c.post("/api/v1/jobs", json={"processId": code, "calculationDate": d})
            r.raise_for_status()
            return finished(c, "jobs/" + r.json()["jobId"])

        def batch():
            r = c.post("/api/v1/batches", json={"calculationDate": day, "factorRevision": 1})
            r.raise_for_status()
            return r.json()["batchId"]

        first = batch()
        submit("refresh-parameters", "2026-11-24")
        record = finished(c, "batches/" + first)
        assert [s["actual"] for s in record["steps"]] == ["250.00", "400.00", "100.00", "500.00", "1150.00"]
        assert all(s["passed"] for s in record["steps"])
        assert all(s["passed"] for s in finished(c, "batches/" + batch())["steps"])
        original = record["steps"][0]["jobId"]
        assert submit("credit-risk", day)["result"]["outputs"][0]["value"] == "375.00"
        r = c.post(f"/api/v1/jobs/{original}/replay", json={})
        result = finished(c, "jobs/" + r.json()["jobId"])
        assert result["replayOf"] == original and result["result"]["outputs"][0]["value"] == "250.00"
        with app.state.runtime.storage.transaction() as conn:
            assert (
                query(
                    conn, "SELECT value FROM resource_values WHERE key='credit_risk' AND partition=:p", p=day
                ).scalar_one()
                == "375.00"
            )
            query(conn, "DELETE FROM resource_values WHERE key='cash_flows' AND partition=:p", p=day)
        failed = finished(c, "batches/" + batch())
        assert "liquidity-risk" in failed["error"]
        assert failed["steps"][-1]["jobId"] is None


def test_period_and_scheduler_history(db, monkeypatch):
    monkeypatch.setenv("SYNTHETIC_START_DATE", "2027-02-01")
    monkeypatch.setenv("SYNTHETIC_END_DATE", "2027-02-03")
    with TestClient(app) as c:
        catalog = c.get("/api/v1/synthetic-cases").json()
        assert [s["calculationDate"] for s in catalog["items"]] == ["2027-02-01", "2027-02-02", "2027-02-03"]
        bad = c.post("/api/v1/jobs", json={"calculationDate": "2026-11-23", "processId": "market-risk"})
        assert bad.status_code == 422 and "03.02.2027" in bad.json()["message"]
        runtime = app.state.runtime
        now = datetime(2027, 2, 2, 10, tzinfo=UTC)
        with runtime.storage.transaction() as conn:
            query(
                conn,
                """INSERT INTO schedules(id,process,due,day) VALUES
            ('miss','market-risk',:old,NULL),('ok','market-risk',:recent,NULL),
            ('outside','market-risk',:recent,'2027-02-04')""",
                old=now - timedelta(hours=1),
                recent=now - timedelta(minutes=1),
            )
        tick(runtime.jobs, now)
        rows = {r["schedule_id"]: r for r in c.get("/api/v1/schedule-history").json()["items"]}
        assert rows["miss"]["status"] == "MISSED"
        assert "DATE_OUTSIDE" in rows["outside"]["reason"]
        assert rows["ok"]["observed"] > rows["ok"]["planned"]
        assert finished(c, "jobs/" + rows["ok"]["job_id"])["status"] == "SUCCEEDED"


def test_retry_limit_and_offline_slot_recovery(db, monkeypatch):
    monkeypatch.setenv("SCHEDULER_POLL_SECONDS", "0.05")
    with TestClient(app) as c:
        runtime = app.state.runtime

        def unavailable(*args):
            raise BrokenPipeError("test worker disconnected")

        monkeypatch.setattr(runtime.worker.compute, "calculate", unavailable)
        response = c.post("/api/v1/jobs", json={"processId": "credit-risk", "calculationDate": "2026-11-23"})
        job = finished(c, "jobs/" + response.json()["jobId"])
        assert job["status"] == "FAILED" and job["attempt"] == 3
        history = c.get("/api/v1/events", params={"jobId": job["jobId"]}).json()["items"]
        assert [e["payload"]["attempt"] for e in history if e["type"] == "JOB_RETRY_SCHEDULED"] == [1, 2]
        runtime.stop.set()
        runtime.wake.set()
        runtime.thread.join()
        with runtime.storage.transaction() as conn:
            query(
                conn,
                """INSERT INTO schedules(id,process,due,day) VALUES
                ('offline','market-risk',now()-interval '2 minutes','2026-11-23'),
                ('missed','market-risk',now()-interval '1 hour','2026-11-23')""",
            )
    with TestClient(app) as c:
        deadline = monotonic() + 5
        while monotonic() < deadline:
            rows = c.get("/api/v1/schedule-history").json()["items"]
            if len(rows) == 2:
                break
            sleep(0.05)
        assert {r["schedule_id"]: r["status"] for r in rows} == {"offline": "DELIVERED", "missed": "MISSED"}
        delivered = next(r for r in rows if r["job_id"])
        assert finished(c, "jobs/" + delivered["job_id"])["status"] == "SUCCEEDED"


def test_factor_change_between_batch_steps(db, monkeypatch):
    from threading import Event

    monkeypatch.setenv("WORKER_SLOTS", "1")
    with TestClient(app) as c:
        entered, release = Event(), Event()
        pool = app.state.runtime.worker.compute
        calculate = pool.calculate

        def hold_first(handler, *args):
            if handler == "credit-risk":
                entered.set()
                assert release.wait(8)
            return calculate(handler, *args)

        monkeypatch.setattr(pool, "calculate", hold_first)
        batch = c.post("/api/v1/batches", json={"factorRevision": 1, "calculationDate": "2026-11-23"}).json()
        try:
            assert entered.wait(5)
            refresh = c.post(
                "/api/v1/jobs",
                json={"processId": "refresh-parameters", "calculationDate": "2026-11-24", "priority": 1},
            ).json()
        finally:
            release.set()
        updated = finished(c, "jobs/" + refresh["jobId"])
        batch = finished(c, "batches/" + batch["batchId"])
        rows = {r["processId"]: r for r in batch["steps"]}
        market = c.get("/api/v1/jobs/" + rows["market-risk"]["jobId"]).json()
        credit = c.get("/api/v1/jobs/" + rows["credit-risk"]["jobId"]).json()
        assert credit["finishedAt"] <= updated["startedAt"] < updated["finishedAt"] <= market["startedAt"]
        assert all(s["passed"] for s in batch["steps"])
        assert batch["factorRevision"] == 1
        assert c.get("/api/v1/factor-revisions").json()["items"][0]["revision"] == 2


def test_replay_idempotency(db, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "proxy")
    monkeypatch.setenv("PUBLIC_ORIGIN", "http://testserver")
    with TestClient(app, headers={"X-Authenticated-User": "analyst"}) as client:
        base = "/api/v1/jobs"
        data = {"processId": "credit-risk", "calculationDate": "2026-11-23"}
        first = client.post(base, json=data, headers={"Idempotency-Key": "shared"})
        second = client.post(base, json=data)
        originals = [response.json()["jobId"] for response in (first, second)]
        for original in originals:
            assert finished(client, "jobs/" + original)["status"] == "SUCCEEDED"

        def replay(original=originals[0], key="shared", actor="analyst"):
            headers = {"X-Authenticated-User": actor}
            if key is not None:
                headers["Idempotency-Key"] = key
            return client.post(f"{base}/{original}/replay", json={}, headers=headers)

        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: replay(), range(2)))
        assert sorted(r.status_code for r in responses) == [200, 201]
        replay_id = responses[0].json()["jobId"]
        assert responses[1].json()["jobId"] == replay_id
        result = finished(client, "jobs/" + replay_id)
        assert result["replayOf"] == originals[0]
        assert result["result"]["outputs"][0]["value"] == "250.00"
        repeated = replay()
        assert repeated.status_code == 200
        receipt = repeated.json()
        assert receipt["jobId"] == replay_id and receipt["status"] == "SUCCEEDED"
        assert datetime.fromisoformat(receipt["queuedAt"]) == datetime.fromisoformat(result["queuedAt"])

        history = client.get("/api/v1/events", params={"jobId": replay_id}).json()["items"]
        assert sum(event["type"] == "JOB_QUEUED" for event in history) == 1
        conflict = replay(originals[1])
        assert conflict.status_code == 409
        assert conflict.json()["code"] == "IDEMPOTENCY_CONFLICT"
        assert replay(key="x" * 129).status_code == 422

        new_replays = [replay(key="new"), replay(key=None), replay(key=None), replay(actor="other")]
        assert all(r.status_code == 201 for r in new_replays)
        assert len({replay_id, *(r.json()["jobId"] for r in new_replays)}) == 5
        with app.state.runtime.storage.engine.connect() as conn:
            source = query(conn, "SELECT source FROM resource_values WHERE key='credit_risk'").scalar_one()
            assert str(source) in originals


def test_validation_error_matches_openapi(db):
    path = "/api/v1/batches"
    with TestClient(app) as client:
        response = client.post(path, json={})
        assert response.status_code == 422
        assert response.json() == {
            "code": "INVALID_CALCULATION_DATE",
            "message": "Укажите календарную дату YYYY-MM-DD",
        }
        schema = client.get("/openapi.json").json()
    validation_schemas = [
        operation["responses"]["422"]["content"]["application/json"]["schema"]
        for path_item in schema["paths"].values()
        for operation in path_item.values()
        if "422" in operation.get("responses", {})
    ]
    batch_schema = schema["paths"][path]["post"]["responses"]["422"]["content"]["application/json"]
    assert batch_schema["schema"] == {"$ref": "#/components/schemas/Failure"}
    assert all(item == batch_schema["schema"] for item in validation_schemas)
    fields = schema["components"]["schemas"]["Failure"]
    assert set(fields["required"]) == set(fields["properties"]) == response.json().keys()
    saved = Path(__file__).resolve().parents[1] / "docs" / "openapi.json"
    assert schema == json.loads(saved.read_text())
