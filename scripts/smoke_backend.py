import argparse
import json
from time import monotonic, sleep
from urllib.request import Request, urlopen
from uuid import uuid4

BASE = "http://127.0.0.1:8000"


def fetch(request):
    with urlopen(request, timeout=5) as response:
        return response.status, json.load(response)


def main():
    if fetch(BASE + "/health/ready")[1]["status"] != "ready":
        raise RuntimeError("Backend ещё не готов")
    case = fetch(BASE + "/api/v1/synthetic-cases")[1]["items"][0]
    request = Request(
        BASE + "/api/v1/jobs",
        data=json.dumps({"processId": "liquidity-risk", "calculationDate": case["calculationDate"]}).encode(),
        headers={"Content-Type": "application/json", "Idempotency-Key": str(uuid4())},
    )
    status, receipt = fetch(request)
    replay_status, replay = fetch(request)
    if status != 201 or replay_status != 200 or receipt["jobId"] != replay["jobId"]:
        raise RuntimeError("Повтор доставки нарушил контракт")
    deadline = monotonic() + 60
    while monotonic() < deadline:
        _, job = fetch(BASE + "/api/v1/jobs/" + receipt["jobId"])
        if job["status"] == "FAILED":
            raise RuntimeError(job["error"])
        if job["status"] == "SUCCEEDED":
            output = job["result"]["outputs"][0]
            if (
                output["resourceKey"] != "liquidity_risk"
                or output["value"] != case["expected"]["liquidity-risk"]
            ):
                raise RuntimeError(output)
            print(job["jobId"], job["status"], output["resourceKey"], output["value"])
            return
        sleep(0.1)
    raise TimeoutError("Истекло время ожидания результата")


def demo():
    request = Request(BASE + "/api/v1/demo", data=b"{}", headers={"Content-Type": "application/json"})
    _, batch = fetch(request)
    first, second, independent = batch["jobs"]
    deadline = monotonic() + 60
    while monotonic() < deadline:
        run = fetch(BASE + "/api/v1/demo/" + batch["demoId"])[1]
        if run["error"]:
            raise RuntimeError(run["error"])
        jobs = run["items"]
        if any(job["error"] for job in jobs):
            raise RuntimeError(jobs)
        if len(jobs) == 3 and all(job["status"] == "SUCCEEDED" for job in jobs):
            break
        sleep(0.2)
    else:
        raise TimeoutError("Демосценарий не завершился")
    sources = [job["trigger"]["type"] for job in jobs]
    if sources != ["MANUAL", "SCHEDULED", "MANUAL"]:
        raise RuntimeError(f"Неожиданные источники {sources}")
    events = {}
    for job in jobs:
        history = fetch(BASE + "/api/v1/events?jobId=" + job["jobId"])[1]
        events.update({(job["jobId"], e["type"]): e["eventId"] for e in history["items"]})
    order = [
        events[second, "JOB_WAITING"],
        events[independent, "JOB_STARTED"],
        events[first, "JOB_SUCCEEDED"],
        events[second, "JOB_RESERVED"],
    ]
    if order != sorted(order):
        raise RuntimeError(f"Неожиданный порядок событий {order}")
    print("demoId", batch["demoId"], "eventIds", order)
    for job in jobs:
        print(
            job["jobId"],
            job["processId"],
            job["trigger"]["type"],
            job["status"],
            job["result"]["outputs"][0]["value"],
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--demo", action="store_true", help="Проверить порядок трёх запусков на стенде с двумя местами"
    )
    if parser.parse_args().demo:
        demo()
    else:
        main()
