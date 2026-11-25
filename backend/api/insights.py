from uuid import UUID

from fastapi import APIRouter, Query, Request
from pydantic import Field

from backend.analytics.report import dependencies, forecast, report
from backend.analytics.simulation import simulate
from backend.api.models import (
    CONFLICT_ERRORS,
    WRITE_ERRORS,
    AnalyticsReport,
    CalculationDate,
    DemoBatch,
    DemoStatus,
    Empty,
    Failure,
    Forecast,
    Insight,
    SimulationResult,
    SyntheticCaseCatalog,
)
from backend.orchestrator.catalog import latest
from backend.orchestrator.demo import create_demo, demo_status
from backend.orchestrator.isolation import job_storage
from backend.orchestrator.resources import conflicts, resolve_accesses
from backend.orchestrator.storage import DomainError, query
from backend.workers.synthetic import case_expectations, cases_for, configured_period

router = APIRouter(prefix="/api/v1")


@router.get("/synthetic-cases", response_model=SyntheticCaseCatalog)
def synthetic_cases(request: Request):
    with request.app.state.runtime.storage.engine.connect() as conn:
        factors = query(
            conn,
            "SELECT value,revision FROM resource_values WHERE key='risk_parameters' AND partition='*'",
        ).one()
    items = []
    for case in cases_for(*configured_period()):
        expected, errors = case_expectations(case.inputs, factors.value)
        items.append(
            {
                "calculationDate": case.day,
                "name": case.name,
                "description": case.description,
                "inputs": case.inputs,
                "expected": expected,
                "expectedErrors": errors,
            }
        )
    start, end = configured_period()
    return {
        "startDate": start,
        "endDate": end,
        "factors": factors.value,
        "factorRevision": factors.revision,
        "items": items,
    }


@router.get("/dependencies")
def graph(request: Request):
    with request.app.state.runtime.storage.engine.connect() as conn:
        return dependencies(conn)


@router.get("/analytics", response_model=AnalyticsReport)
def analytics(request: Request):
    with request.app.state.runtime.storage.engine.connect() as conn:
        return report(conn)


@router.get("/analytics/forecast", response_model=Forecast)
def predict(processId: str, calculationDate: CalculationDate, request: Request):
    with request.app.state.runtime.storage.engine.connect() as conn:
        return forecast(conn, latest(conn, processId), calculationDate)


class Question(Empty):
    question: str = Field(min_length=1, max_length=500)
    jobId: UUID | None = None
    calculationDate: CalculationDate | None = None


@router.post("/analytics/ask", response_model=Insight, responses={404: {"model": Failure}, **WRITE_ERRORS})
def ask(body: Question, request: Request):
    body.calculationDate = body.calculationDate or configured_period()[0]
    question = body.question.casefold()
    with job_storage(request.app.state.runtime, body.jobId).engine.connect() as conn:
        if body.jobId:
            row = query(conn, "SELECT status,reason,error FROM jobs WHERE id=:id", id=body.jobId).first()
            if not row:
                raise DomainError("JOB_NOT_FOUND", "Задача не найдена", 404)
            reason = row.reason or {}
            details = ", ".join(
                f"{c['resourceKey']} ({c['requestedMode']}/{c['activeMode']}, задача {c['blockingJobId']})"
                for c in reason.get("conflicts", [])
            )
            answer = f"Статус {row.status}. " + (
                details or reason.get("code") or (row.error or {}).get("message", "")
            )
            return {
                "answer": answer,
                "evidence": {"jobId": body.jobId, "reason": row.reason},
            }
        if any(word in question for word in ("почему", "заблокирован")):
            waiting = (
                query(
                    conn,
                    "SELECT id,process,reason FROM jobs WHERE status='WAITING' ORDER BY seq LIMIT 10",
                )
                .mappings()
                .all()
            )
            return {
                "answer": "Выберите ID ожидающей задачи для разбора её блокировок."
                if waiting
                else "Сейчас ожидающих задач нет.",
                "evidence": [dict(row) for row in waiting],
            }
        if any(word in question for word in ("паралл", "одноврем", "запуст", "совмест")):
            definitions = query(
                conn,
                "SELECT DISTINCT ON(process_id) * FROM processes ORDER BY process_id,version DESC",
            ).all()
            chosen: list[str] = []
            owners: list[dict] = []
            for d in definitions:
                if not d.active or forecast(conn, d, body.calculationDate)["conflictsNow"]:
                    continue
                accesses = resolve_accesses(d.access_templates, body.calculationDate)
                if not conflicts(accesses, owners):
                    chosen.append(d.process_id)
                    owners.extend(dict(a, jobId=d.process_id) for a in accesses)
            return {
                "answer": "Совместимый по ресурсам набор на выбранную дату: " + (", ".join(chosen) or "нет"),
                "evidence": {"date": body.calculationDate, "compatible": chosen},
            }
        stats = report(conn)
        if any(w in question for w in ("распис", "сократ", "перенес", "оптим")):
            pairs = list(
                query(
                    conn,
                    """SELECT j.process, owner.process AS blocker, count(DISTINCT j.id) AS jobs,
                ceil(max(extract(epoch FROM owner.finished-owner.started))/60) AS minutes
                FROM job_events e JOIN jobs j ON j.id=e.job_id
                CROSS JOIN LATERAL jsonb_array_elements(e.payload->'waitReason'->'conflicts') c
                JOIN jobs owner ON owner.id=(c->>'blockingJobId')::uuid
                WHERE j.trigger->>'type'='SCHEDULED' AND e.occurred>now()-interval '7 days'
                AND e.payload->'waitReason'->>'code'='RESOURCE_CONFLICT'
                GROUP BY j.process,owner.process ORDER BY jobs DESC LIMIT 1""",
                ).mappings()
            )
            if pairs and pairs[0]["minutes"] is not None:
                p = pairs[0]
                return {
                    "answer": f"Проверьте перенос {p['process']} на {max(1, p['minutes'])} мин "
                    f"позже запуска {p['blocker']}. "
                    f"У {p['jobs']} регламентных задач наблюдался такой конфликт. "
                    "Оценка основана на длительности блокирующего расчёта.",
                    "evidence": [dict(row) for row in pairs],
                }
            return {
                "answer": (
                    "Для оценки переноса нужны завершённые блокирующие расчёты "
                    "и конфликты регламентных задач за последние 7 дней."
                ),
                "evidence": [dict(row) for row in pairs],
            }
        if "процесс" in question:
            ranked = sorted(stats["processes"], key=lambda x: x["blocked"], reverse=True)
            return {
                "answer": (
                    f"Чаще всего ждал ресурс {ranked[0]['process']}: "
                    f"{ranked[0]['blocked']} запусков за 7 дней."
                    if ranked and ranked[0]["blocked"]
                    else "Конфликтов процессов за 7 дней не зафиксировано."
                ),
                "evidence": [dict(row) for row in ranked],
            }
        top = stats["resources"][:5]
        return {
            "answer": (
                f"Больше всего времени ожидания приходится на {top[0]['resourceKey']}: "
                f"{top[0]['waitSeconds']} с за 7 дней."
                if top
                else "Ожидания ресурсов за последние 7 дней не зафиксированы."
            ),
            "evidence": top,
        }


@router.get("/simulation", response_model=SimulationResult, response_model_exclude_unset=True)
def simulation(
    seed: int = 42,
    count: int = Query(40, ge=2, le=200),
    slots: int = Query(2, ge=1, le=20),
):
    return simulate(seed, count, slots)


@router.post("/demo", status_code=201, response_model=DemoBatch, responses=CONFLICT_ERRORS)
def demo(body: Empty, request: Request):
    return create_demo(request.app.state.runtime.demo, request.state.actor)


@router.get(
    "/demo/{batch_id}", response_model=DemoStatus, response_model_exclude_unset=True,
    responses={404: {"model": Failure}},
)
def read_demo(batch_id: UUID, request: Request):
    runtime = request.app.state.runtime
    with runtime.demo.storage.engine.connect() as conn:
        if query(conn, "SELECT 1 FROM demo_runs WHERE id=:id", id=batch_id).first():
            return demo_status(conn, batch_id)
    with runtime.storage.engine.connect() as conn:
        return demo_status(conn, batch_id)
