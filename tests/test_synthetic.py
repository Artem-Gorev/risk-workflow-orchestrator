from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from decimal import Decimal
from time import monotonic, sleep

from fastapi.testclient import TestClient

from backend.app import app
from backend.workers.synthetic import DAYS, GENERATED_START, PORTFOLIO_SIZE, portfolios


def test_generation_by_date():
    initial = dict(portfolios())
    assert initial == dict(portfolios())
    assert initial != dict(portfolios(seed=17))
    day = GENERATED_START + timedelta(days=1)
    assert dict(portfolios(start=day, days=1))[day] == initial[day]


def test_synthetic_portfolios_through_api(db):
    data = dict(portfolios())
    assert len(data) == DAYS
    expected = {}
    for day, values in data.items():
        exposures, positions = values["credit_exposures"], values["market_positions"]
        assert len(exposures) == len(positions) == PORTFOLIO_SIZE
        incoming, outgoing = values["cash_flows"]
        expected[day, "credit-risk"] = Decimal(sum(exposures)) / 10
        expected[day, "market-risk"] = Decimal(sum(map(abs, positions))) / 5
        expected[day, "liquidity-risk"] = Decimal(max(0, outgoing - incoming))

    with TestClient(app) as client:

        def run(item):
            day, process = item
            response = client.post("/api/v1/jobs", json={"processId": process, "calculationDate": str(day)})
            assert response.status_code == 201, response.text
            job_id = response.json()["jobId"]
            deadline = monotonic() + 30
            while monotonic() < deadline:
                response = client.get(f"/api/v1/jobs/{job_id}")
                response.raise_for_status()
                job = response.json()
                if job["status"] == "SUCCEEDED":
                    return job["result"]["outputs"][0]["value"]
                assert job["status"] != "FAILED", job["error"]
                sleep(0.05)
            raise AssertionError(f"Расчёт {job_id} не завершился вовремя")

        with ThreadPoolExecutor(8) as pool:
            results = pool.map(run, expected)
            for result, target in zip(results, expected.values(), strict=True):
                assert Decimal(result) == target
            days = list(data)
            summaries = pool.map(run, [(day, "risk-summary") for day in days])
            for day, total in zip(days, summaries, strict=True):
                assert Decimal(total) == sum(
                    expected[day, p] for p in ("credit-risk", "market-risk", "liquidity-risk")
                )

        last = days[-1]
        credit, market = data[last]["parameter_inputs"]
        factors = run((last, "refresh-parameters"))
        assert Decimal(factors["creditFactor"]) == Decimal(credit) / 100
        assert Decimal(factors["marketFactor"]) == Decimal(market) / 100
        assert (
            Decimal(run((last, "credit-risk"))) == Decimal(sum(data[last]["credit_exposures"]) * credit) / 100
        )


def test_named_cases_have_reproducible_expected_outcomes(db):
    with TestClient(app) as client:
        base = "/api/v1"
        catalog = client.get(base + "/synthetic-cases").json()
        cases = {item["calculationDate"]: item for item in catalog["items"]}
        assert sorted(cases) == [f"2026-11-{day}" for day in range(23, 31)]
        for day in ("2026-11-22", "2026-12-01", "2026-04-01"):
            rejected = client.post(base + "/jobs", json={"processId": "credit-risk", "calculationDate": day})
            assert rejected.status_code == 422
            assert rejected.json()["code"] == "DATE_OUTSIDE_SYNTHETIC_RANGE"
        assert client.get(base + "/events").json()["items"] == []

        def execute(day, process, terminal="SUCCEEDED"):
            submitted = client.post(base + "/jobs", json={"processId": process, "calculationDate": day})
            submitted.raise_for_status()
            job_id = submitted.json()["jobId"]
            deadline = monotonic() + 10
            while monotonic() < deadline:
                job = client.get(f"{base}/jobs/{job_id}").json()
                if job["status"] in {"SUCCEEDED", "FAILED", "CANCELLED"}:
                    assert job["status"] == terminal, job
                    return job
                sleep(0.05)
            raise AssertionError(f"Расчёт {job_id} не завершился вовремя")

        missing = execute("2026-11-23", "risk-summary", terminal="FAILED")
        assert missing["error"]["code"] == "INPUT_NOT_FOUND"
        assert missing["leaseUntil"] is None
        assert (
            execute("2026-11-30", "operational-risk")["result"]["outputs"][0]["value"]
            == cases["2026-11-30"]["expected"]["operational-risk"]
        )

        processes = ("credit-risk", "market-risk", "liquidity-risk", "operational-risk", "risk-summary")
        for day, values in (
            ("2026-11-23", ("250.00", "400.00", "500.00", "100.00", "1150.00")),
            ("2026-11-24", ("4000.00", "10000.00", "8000.00", "2500.00", "22000.00")),
            ("2026-11-25", ("0.00",) * 5),
        ):
            for process, expected in zip(processes, values, strict=True):
                assert cases[day]["expected"][process] == expected
                result = execute(day, process)["result"]
                assert result["outputs"][0]["value"] == expected

        update = execute("2026-11-24", "refresh-parameters")
        catalog_after = client.get(base + "/synthetic-cases").json()
        assert catalog_after["factorRevision"] > catalog["factorRevision"]
        assert catalog_after["items"][0]["expected"]["credit-risk"] == "375.00"
        rerun = execute("2026-11-23", "credit-risk")["result"]
        assert rerun["outputs"][0]["value"] == "375.00"
        assert any(item["sourceJobId"] == update["jobId"] for item in rerun["inputVersions"])
