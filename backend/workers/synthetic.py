import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date, timedelta
from decimal import Decimal
from functools import lru_cache
from random import Random

START_DATE = date(2026, 11, 23)
END_DATE = date(2026, 11, 30)
GENERATED_START = date(2026, 11, 28)
DAYS = (END_DATE - GENERATED_START).days + 1
PORTFOLIO_SIZE = 256
SEED = 20261001


def portfolios(
    start: date = GENERATED_START,
    days: int = DAYS,
    size: int = PORTFOLIO_SIZE,
    seed: int = SEED,
) -> Iterator[tuple[date, dict]]:
    if days < 1 or size < 1:
        raise ValueError("Число дней и размер портфеля должны быть положительными")
    for offset in range(days):
        day = start + timedelta(days=offset)
        rng = Random(f"{seed}:{day.isoformat()}")
        stress = day.day % 7 == 0
        exposures = [
            rng.randint(1, 5000) if rng.random() < (0.45 if stress else 0.08) else 0 for _ in range(size)
        ]
        spread = 8000 if stress else 2000
        positions = [rng.randint(-spread, spread) for _ in range(size)]
        incoming = rng.randint(100, 500) * size
        outgoing = incoming + rng.randint(1, incoming // 2) if stress else rng.randint(0, 2 * incoming)
        if day.day == 1:
            exposures = [0] * size
            outgoing = incoming
        yield (
            day,
            {
                "credit_exposures": exposures,
                "market_positions": positions,
                "cash_flows": [incoming, outgoing],
                "incident_losses": [rng.randint(50, 1000) for _ in range(rng.randrange(1, 8))],
                "parameter_inputs": [
                    rng.randint(12, 25) if stress else rng.randint(3, 12),
                    rng.randint(20, 35) if stress else rng.randint(5, 20),
                ],
            },
        )


@dataclass(frozen=True)
class SyntheticCase:
    day: date
    name: str
    description: str
    inputs: dict[str, list[int]]


SYNTHETIC_CASES = (
    SyntheticCase(
        START_DATE,
        "Базовый портфель",
        "Обычный день с небольшим дефицитом ликвидности.",
        {
            "credit_exposures": [1200, 800, 0, 500],
            "market_positions": [1000, -700, 300],
            "cash_flows": [9500, 10000],
            "incident_losses": [0, 80, 20],
            "parameter_inputs": [10, 20],
        },
    ),
    SyntheticCase(
        date(2026, 11, 24),
        "Стрессовый день",
        "Высокая просрочка, крупные позиции и отток денежных средств.",
        {
            "credit_exposures": [18000, 12500, 9500, 0],
            "market_positions": [16000, -25000, 9000],
            "cash_flows": [12000, 20000],
            "incident_losses": [700, 1200, 600],
            "parameter_inputs": [15, 25],
        },
    ),
    SyntheticCase(
        date(2026, 11, 25),
        "Нулевые риски",
        "Нет просрочки, открытых позиций и инцидентов. Поступления покрывают выплаты.",
        {
            "credit_exposures": [0, 0],
            "market_positions": [0, 0],
            "cash_flows": [15000, 12000],
            "incident_losses": [],
            "parameter_inputs": [10, 20],
        },
    ),
    SyntheticCase(
        date(2026, 11, 26),
        "Контрольный портфель A",
        "Малые суммы для ручной сверки формул.",
        {
            "credit_exposures": [40, 60],
            "market_positions": [-30, 20],
            "cash_flows": [70, 100],
            "incident_losses": [4, 6],
            "parameter_inputs": [10, 20],
        },
    ),
    SyntheticCase(
        date(2026, 11, 27),
        "Контрольный портфель B",
        "Второй независимый раздел данных за другую дату.",
        {
            "credit_exposures": [50, 100],
            "market_positions": [-40, 20],
            "cash_flows": [80, 110],
            "incident_losses": [12, 18],
            "parameter_inputs": [15, 25],
        },
    ),
    *(
        SyntheticCase(
            day,
            "Расширенный портфель",
            "256 кредитных сумм и 256 рыночных позиций.",
            inputs,
        )
        for day, inputs in portfolios()
    ),
)


def configured_period():
    start = date.fromisoformat(os.getenv("SYNTHETIC_START_DATE", START_DATE.isoformat()))
    end = date.fromisoformat(os.getenv("SYNTHETIC_END_DATE", END_DATE.isoformat()))
    if end < start:
        raise ValueError("Конец синтетического периода раньше начала")
    return start, end


@lru_cache(maxsize=2)
def cases_for(start, end):
    named = {case.day: case for case in SYNTHETIC_CASES}
    return tuple(
        named.get(day)
        or SyntheticCase(
            day,
            "Расширенный портфель",
            "256 кредитных сумм и 256 рыночных позиций.",
            inputs,
        )
        for day, inputs in portfolios(start, (end - start).days + 1)
    )


def in_synthetic_period(day: date) -> bool:
    start, end = configured_period()
    return start <= day <= end


def period_label():
    start, end = configured_period()
    return f"{start:%d.%m.%Y} – {end:%d.%m.%Y}"


def case_expectations(resources, factors):
    amounts = {
        "credit-risk": sum(resources["credit_exposures"]) * Decimal(factors["creditFactor"]),
        "liquidity-risk": max(0, resources["cash_flows"][1] - resources["cash_flows"][0]),
        "operational-risk": sum(resources["incident_losses"]),
    }
    errors = {}
    if "market_positions" in resources:
        market = sum(map(abs, resources["market_positions"])) * Decimal(factors["marketFactor"])
        amounts["market-risk"] = market
        amounts["risk-summary"] = amounts["credit-risk"] + market + amounts["liquidity-risk"]
    else:
        errors = {"market-risk": "INPUT_NOT_FOUND", "risk-summary": "INPUT_NOT_FOUND"}
    expected: dict[str, str | dict[str, str]] = {code: f"{value:.2f}" for code, value in amounts.items()}
    credit, market = resources["parameter_inputs"]
    expected["refresh-parameters"] = {
        "creditFactor": f"{Decimal(credit) / 100:.2f}",
        "marketFactor": f"{Decimal(market) / 100:.2f}",
    }
    return expected, errors
