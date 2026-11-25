import json
import os
import signal
from concurrent.futures import ThreadPoolExecutor
from contextlib import suppress
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from time import monotonic, sleep
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.engine import make_url

from backend import runtime as runtime_module
from backend.api import management
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
        restart = c.post(f"/api/v1/jobs/{original}/restart", json={})
        assert restart.status_code == 409 and restart.json()["code"] == "BATCH_STEP_RESTART"
        with app.state.runtime.storage.engine.connect() as conn:
            count = query(conn, "SELECT count(*) FROM resource_values WHERE key='credit_risk'").scalar_one()
            assert count == 0
        assert submit("credit-risk", day)["result"]["outputs"][0]["value"] == "375.00"
        assert finished(c, "batches/" + batch())["status"] == "SUCCEEDED"
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
        assert finished(c, "jobs/" + failed["steps"][0]["jobId"])["status"] == "SUCCEEDED"
        with app.state.runtime.storage.engine.connect() as conn:
            value = query(conn, "SELECT value FROM resource_values WHERE key='credit_risk'").scalar_one()
            count = query(conn, "SELECT count(*) FROM resource_values WHERE key='risk_summary'").scalar_one()
            assert value == "375.00" and count == 0


def test_batch_with_mismatched_values_fails(db):
    with TestClient(app) as client:
        day = "2026-11-26"
        with app.state.runtime.storage.transaction() as conn:
            query(
                conn,
                "UPDATE resource_values SET value='[200]' WHERE key='credit_exposures' AND partition=:day",
                day=day,
            )
        batch = client.post("/api/v1/batches", json={"calculationDate": day, "factorRevision": 1})
        assert batch.status_code == 201
        record = finished(client, "batches/" + batch.json()["batchId"])
        assert record["status"] == "FAILED"
        assert "credit-risk" in record["error"] and "risk-summary" in record["error"]
        assert record["steps"][-1]["actual"] == "60.00" and record["steps"][-1]["passed"] is False


def test_batch_delivery_is_idempotent_under_concurrent_submissions(db, monkeypatch):
    monkeypatch.setenv("AUTH_MODE", "proxy")
    monkeypatch.setenv("PUBLIC_ORIGIN", "http://testserver")
    with TestClient(app, headers={"X-Authenticated-User": "author"}) as client:
        body = {"calculationDate": "2026-11-26", "factorRevision": 1}

        def post(actor="author", payload=body):
            return client.post(
                "/api/v1/batches", json=payload,
                headers={"Idempotency-Key": "same", "X-Authenticated-User": actor},
            )

        with ThreadPoolExecutor(2) as pool:
            responses = list(pool.map(lambda _: post(), range(2)))
        assert sorted(response.status_code for response in responses) == [200, 201]
        assert responses[0].json() == responses[1].json()
        incompatible = post(payload=body | {"factorRevision": 2})
        assert incompatible.status_code == 409 and incompatible.json()["code"] == "IDEMPOTENCY_CONFLICT"
        separate = post(actor="another")
        assert separate.status_code == 201 and separate.json() != responses[0].json()


def test_historical_batch_does_not_reserve_shared_outputs_or_factors(db, monkeypatch):
    monkeypatch.setenv("WORKER_SLOTS", "2")
    with TestClient(app) as client:
        entered, release = Event(), Event()
        pool = app.state.runtime.worker.compute
        calculate = pool.calculate

        def hold_batch_credit(handler, *args):
            if handler == "credit-risk" and not entered.is_set():
                entered.set()
                assert release.wait(12)
            return calculate(handler, *args)

        monkeypatch.setattr(pool, "calculate", hold_batch_credit)
        day = "2026-11-26"
        batch = client.post("/api/v1/batches", json={"calculationDate": day, "factorRevision": 1})
        assert batch.status_code == 201
        batch_path = "batches/" + batch.json()["batchId"]
        try:
            assert entered.wait(5)
            credit = client.get("/api/v1/" + batch_path).json()["steps"][0]
            owners = client.get("/api/v1/dependencies").json()["owners"]
            owned_resources = [r["resource_key"] for r in owners if r["job_id"] == credit["jobId"]]
            assert owned_resources == ["credit_exposures"]
            for process, calc_day in (("credit-risk", day), ("refresh-parameters", "2026-11-27")):
                response = client.post(
                    "/api/v1/jobs", json={"processId": process, "calculationDate": calc_day}
                )
                assert response.status_code == 201
                assert finished(client, "jobs/" + response.json()["jobId"])["status"] == "SUCCEEDED"
        finally:
            release.set()
        assert finished(client, batch_path)["status"] == "SUCCEEDED"


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


def test_stalled_child_times_out_and_releases_slot(db, monkeypatch):
    monkeypatch.setenv("WORKER_SLOTS", "1")
    monkeypatch.setenv("WORKER_TIMEOUT_SECONDS", "1.5")
    monkeypatch.setenv("WORKER_DELAY_SECONDS", "0.3")
    with TestClient(app) as client:
        runtime = app.state.runtime
        pool = runtime.worker.compute

        def submit(process):
            response = client.post(
                "/api/v1/jobs", json={"processId": process, "calculationDate": "2026-11-26"}
            )
            response.raise_for_status()
            return response.json()["jobId"]

        first = submit("credit-risk")
        deadline = monotonic() + 5
        while not pool.channels and monotonic() < deadline:
            sleep(0.01)
        pid = next(iter(pool.channels))
        os.kill(pid, signal.SIGSTOP)
        try:
            independent = submit("liquidity-risk")
            assert finished(client, "jobs/" + independent)["status"] == "SUCCEEDED"
            recovered = finished(client, "jobs/" + first)
            assert recovered["status"] == "SUCCEEDED" and recovered["attempt"] == 2
            assert pid not in pool.channels
            events = client.get("/api/v1/events", params={"jobId": first}).json()["items"]
            scheduled = [event for event in events if event["type"] == "JOB_RETRY_SCHEDULED"]
            assert [event["payload"]["error"]["code"] for event in scheduled] == ["EXECUTION_TIMEOUT"]
        finally:
            if pid in pool.channels:
                with suppress(ProcessLookupError):
                    os.kill(pid, signal.SIGCONT)


def test_schedule_put_preserves_due_deliveries_and_missed_history(db, monkeypatch):
    class Clock:
        time = datetime(2026, 11, 25, 22, 59, tzinfo=UTC)

        @classmethod
        def now(cls, tz):
            return cls.time.astimezone(tz)

    monkeypatch.setattr(management, "datetime", Clock)
    monkeypatch.setattr(runtime_module, "tick", lambda service, now: None)
    with TestClient(app) as client:
        body = {"processId": "credit-risk", "frequency": "DAILY", "localTime": "02:00"}

        def save(code, **changes):
            response = client.put(f"/api/v1/schedules/{code}", json=body | changes)
            response.raise_for_status()
            return response.json()["nextFireAt"]

        planned = save("same")
        assert save("changed") == planned
        assert save("expired", localTime="02:10") != planned
        Clock.time += timedelta(minutes=2)
        assert save("same") == planned
        assert save("changed", enabled=False) is None
        tick(app.state.runtime.jobs, Clock.time)
        slots = {slot["schedule_id"]: slot for slot in client.get("/api/v1/schedule-history").json()["items"]}
        assert slots["same"]["status"] == "DELIVERED"
        assert slots["changed"]["status"] == "DELIVERED"
        Clock.time += timedelta(days=1, minutes=16)
        assert save("expired", localTime="03:00") == "2026-11-27T00:00:00Z"
        history = client.get("/api/v1/schedule-history").json()["items"]
        assert [s["status"] for s in history if s["schedule_id"] == "expired"] == ["MISSED", "MISSED"]


def test_one_off_schedule_keeps_its_planned_instant_and_date(db, monkeypatch):
    monkeypatch.setattr(runtime_module, "tick", lambda service, now: None)
    with TestClient(app) as client:
        service = app.state.runtime.jobs
        planned = datetime.now(UTC) + timedelta(hours=1)
        with service.storage.transaction() as conn:
            query(
                conn,
                "INSERT INTO schedules(id,process,due,day) VALUES ('once','credit-risk',:due,'2026-11-26')",
                due=planned,
            )
        url = "/api/v1/schedules/once"
        recurring = client.put(url, json={
            "processId": "credit-risk", "frequency": "DAILY", "localTime": "02:00",
        })
        assert recurring.status_code == 409
        single = {"processId": "credit-risk", "frequency": "ONCE"}
        assert client.put(url, json=single | {"enabled": False}).json()["nextFireAt"] is None
        tick(service, planned)
        assert client.get("/api/v1/schedule-history").json()["items"] == []
        saved = client.put(url, json=single)
        assert saved.status_code == 200 and datetime.fromisoformat(saved.json()["nextFireAt"]) == planned
        schedule = client.get("/api/v1/schedules").json()["items"][0]
        assert schedule["daily"] is False and schedule["day"] == "2026-11-26"
        tick(service, planned)
        tick(service, planned + timedelta(days=1))
        record, = client.get("/api/v1/schedule-history").json()["items"]
        assert record["status"] == "DELIVERED"
        delivered = finished(client, "jobs/" + record["job_id"])
        assert delivered["calculationDate"] == "2026-11-26"
        assert client.get("/api/v1/schedules").json()["items"][0]["due"] is None


def test_api_serializes_timestamps_as_utc_in_moscow_session(db, monkeypatch):
    url = make_url(os.environ["DATABASE_URL"])
    url = url.update_query_dict({"options": url.query["options"] + " -c TimeZone=Europe/Moscow"})
    monkeypatch.setenv("DATABASE_URL", url.render_as_string(hide_password=False))
    with TestClient(app) as client:
        receipt = client.post(
            "/api/v1/jobs", json={"processId": "credit-risk", "calculationDate": "2026-11-26"}
        ).json()
        detail = finished(client, "jobs/" + receipt["jobId"])
        events = client.get("/api/v1/events", params={"jobId": receipt["jobId"]}).json()["items"]
        assert receipt["queuedAt"] == detail["queuedAt"] == events[0]["occurredAt"]
        assert all(event["occurredAt"].endswith("Z") for event in events)
        analytics = client.get("/api/v1/analytics").json()
        assert all(point["minute"].endswith("Z") for point in analytics["load"])
        assert all(event["occurred"].endswith("Z") for event in analytics["recentEvents"])


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
        epoch = int(datetime(2026, 11, 26, tzinfo=UTC).timestamp())
        for invalid in (epoch, str(epoch), "2026-11-26T00:00:00Z"):
            batch = client.post(path, json={"calculationDate": invalid, "factorRevision": 1})
            date_filter = client.get("/api/v1/jobs", params={"calculationDate": invalid})
            prediction = client.get(
                "/api/v1/analytics/forecast", params={"processId": "credit-risk", "calculationDate": invalid}
            )
            question = client.post(
                "/api/v1/analytics/ask", json={"question": "Очередь", "calculationDate": invalid}
            )
            assert all(
                r.status_code == 422 and r.json() == response.json()
                for r in (batch, date_filter, prediction, question)
            )
        missing = client.post(f"/api/v1/jobs/{uuid4()}/stop", json={})
        submitted = client.post(
            "/api/v1/jobs", json={"processId": "credit-risk", "calculationDate": "2026-11-26"}
        ).json()["jobId"]
        assert finished(client, "jobs/" + submitted)["status"] == "SUCCEEDED"
        terminal = client.post(f"/api/v1/jobs/{submitted}/stop", json={})
        declared = schema["paths"]["/api/v1/jobs/{job_id}/stop"]["post"]["responses"]
        for status, result, code in ((404, missing, "JOB_NOT_FOUND"), (409, terminal, "JOB_STATE_CONFLICT")):
            assert result.status_code == status and result.json()["code"] == code
            assert declared[str(status)]["content"]["application/json"]["schema"] == {
                "$ref": "#/components/schemas/Failure"
            }
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
