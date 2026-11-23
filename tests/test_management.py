from concurrent.futures import ThreadPoolExecutor
from time import monotonic, sleep

from fastapi.testclient import TestClient

from backend.app import app
from backend.orchestrator.storage import query


def wait(client, job, status):
    end = monotonic() + 10
    while monotonic() < end:
        response = client.get("/api/v1/jobs/" + job)
        if response.status_code == 404:
            sleep(0.03)
            continue
        record = response.json()
        if record["status"] == status:
            return record
        sleep(0.03)
    raise AssertionError(f"{job} did not reach {status}: {response.text}")


def test_management_and_risk(db):
    with TestClient(app) as client:
        base = "/api/v1"
        assert client.get("/docs").status_code == 200
        assert (
            client.post(
                base + "/resources", json={"key": "op_branch", "kind": "operational_risk"}
            ).status_code
            == 201
        )
        definition = {
            "handler": "operational-risk",
            "name": "Отделение",
            "executionSeconds": 2,
            "priority": 2,
            "joinActive": True,
            "bindings": {"operational_risk": "op_branch"},
        }
        assert client.put(base + "/processes/branch", json=definition).json()["processVersion"] == 1
        assert client.put(base + "/processes/branch", json=definition).status_code == 409
        payload = {"processId": "branch", "calculationDate": "2026-11-23"}

        def deliver(i):
            return client.post(base + "/jobs", json=payload, headers={"Idempotency-Key": str(i)})

        with ThreadPoolExecutor(20) as pool:
            responses = list(pool.map(deliver, range(20)))
        assert sum(r.status_code == 201 for r in responses) == 1
        ids = {r.json()["jobId"] for r in responses}
        assert len(ids) == 1
        job = ids.pop()
        wait(client, job, "RUNNING")
        forecast = client.get(base + "/analytics/forecast", params=payload).json()
        assert forecast["conflictsNow"][0]["blockingJobId"] == job
        assert client.post(base + "/jobs/" + job + "/stop", json={}).status_code == 200
        assert deliver(19).json()["jobId"] == job
        repeat = client.post(base + "/jobs/" + job + "/restart", json={}).json()["jobId"]
        assert repeat != job
        updated = client.put(
            base + "/processes/branch", json={**definition, "expectedVersion": 1, "isActive": False}
        )
        assert updated.status_code == 200
        assert client.post(base + "/jobs", json=payload).status_code == 409
        done = wait(client, repeat, "SUCCEEDED")
        assert done["processVersion"] == 1 and done["result"]["outputs"][0]["resourceKey"] == "op_branch"
        schedule = {
            "processId": "operational-risk",
            "frequency": "WEEKLY",
            "weekday": 0,
            "localTime": "04:00",
        }
        assert client.put(base + "/schedules/monday", json=schedule).status_code == 200
        row = next(s for s in client.get(base + "/schedules").json()["items"] if s["id"] == "monday")
        assert row["weekday"] == 0 and row["local_time"] == "04:00:00"
        assert client.get(base + "/audit/configuration").json()["items"][-1]["operation"] == "SCHEDULE_SAVED"
        assert client.get(base + "/dependencies").status_code == 200
        assert client.get(base + "/analytics").json()["totalJobs"] == 2
        for question in (
            "Какие процессы конфликтуют?",
            "Как оптимизировать расписание?",
            "Что запустить параллельно?",
        ):
            assert client.post(base + "/analytics/ask", json={"question": question}).json()["answer"]
        result = client.get(base + "/simulation").json()
        assert result["managed"]["conflictingPairs"] == 0 < result["unmanaged"]["conflictingPairs"]


def test_child_crash_and_expired_lease(db):
    with TestClient(app) as client:
        runtime = app.state.runtime
        client.put(
            "/api/v1/processes/slow",
            json={"handler": "credit-risk", "name": "Длинный расчёт", "executionSeconds": 2},
        )

        def launch(code):
            return client.post(
                "/api/v1/jobs", json={"processId": code, "calculationDate": "2026-11-23"}
            ).json()["jobId"]

        first = launch("slow")
        before = wait(client, first, "RUNNING")["leaseUntil"]
        end = monotonic() + 5
        while monotonic() < end:
            if client.get("/api/v1/jobs/" + first).json()["leaseUntil"] != before:
                break
            sleep(0.05)
        else:
            raise AssertionError("heartbeat did not renew")
        pool = runtime.worker.compute
        with pool.lock:
            child = next(iter(pool.channels.values()))[1]
            child.terminate()
        assert wait(client, first, "SUCCEEDED")["attempt"] == 2
        events = client.get("/api/v1/events", params={"jobId": first}).json()["items"]
        (retry,) = [e for e in events if e["type"] == "JOB_RETRY_SCHEDULED"]
        assert retry["payload"]["error"]["code"] == "WORKER_LOST"
        assert sum(e["type"] == "JOB_SUCCEEDED" for e in events) == 1
        second = launch("slow")
        wait(client, second, "RUNNING")
        with runtime.storage.transaction() as conn:
            query(conn, "UPDATE jobs SET lease_until=now()-interval '1 second' WHERE id=:id", id=second)
        assert wait(client, second, "SUCCEEDED")["attempt"] == 2
        third = launch("credit-risk")
        result = wait(client, third, "SUCCEEDED")
        assert result["result"]["outputs"][0]["revision"] == 3


def test_live_demo_explains_conflict(db):
    with TestClient(app) as client:
        client.put(
            "/api/v1/processes/foreground",
            json={"handler": "credit-risk", "name": "Рабочая нагрузка", "executionSeconds": 2},
        )
        main = client.post(
            "/api/v1/jobs", json={"processId": "foreground", "calculationDate": "2026-11-23"}
        ).json()["jobId"]
        wait(client, main, "RUNNING")
        response = client.post("/api/v1/demo", json={})
        assert response.status_code == 201
        first, second, third = response.json()["jobs"]
        wait(client, second, "WAITING")
        assert client.post("/api/v1/demo", json={}).json()["code"] == "DEMO_QUEUE_BUSY"
        batch = client.get("/api/v1/demo/" + response.json()["demoId"]).json()
        assert batch["status"] == "DELIVERED"
        assert [job["trigger"]["type"] for job in batch["items"]] == ["MANUAL", "SCHEDULED", "MANUAL"]
        assert batch["items"][1]["trigger"]["scheduleId"].startswith("demo-")
        events_by_job = client.get("/api/v1/events", params={"jobId": second}).json()["items"]
        assert sum(e["type"] == "JOB_QUEUED" for e in events_by_job) == 1
        explanation = client.post(
            "/api/v1/analytics/ask", json={"question": "Почему задача ждёт?", "jobId": second}
        ).json()
        assert first in explanation["answer"]
        wait(client, second, "SUCCEEDED")
        events = [
            e
            for job in (first, second, third)
            for e in client.get("/api/v1/events", params={"jobId": job}).json()["items"]
        ]
        order = {(e["jobId"], e["type"]): e["eventId"] for e in events}
        assert order[third, "JOB_STARTED"] < order[first, "JOB_SUCCEEDED"] < order[second, "JOB_RESERVED"]
        stats = client.get("/api/v1/analytics").json()
        assert stats["totalJobs"] == 1
        assert stats["conflictedJobs"] == 0
