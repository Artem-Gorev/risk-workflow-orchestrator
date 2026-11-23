from collections.abc import Callable
from dataclasses import dataclass
from decimal import ROUND_HALF_UP, Decimal


@dataclass(frozen=True)
class Process:
    code: str
    name: str
    inputs: tuple[str, ...]
    outputs: tuple[str, ...]
    calculate: Callable[[dict], dict]

    def accesses(self, day: str) -> list[dict]:
        modes = dict.fromkeys(self.inputs, "READ")
        modes.update(dict.fromkeys(self.outputs, "WRITE"))
        return [{"resourceKey": key, "scope": scope(key, day), "mode": mode} for key, mode in modes.items()]

    def templates(self):
        return [
            {
                "resourceKey": entry["resourceKey"],
                "scope": "GLOBAL" if entry["scope"]["type"] == "GLOBAL" else "JOB_DATE",
                "mode": entry["mode"],
            }
            for entry in self.accesses("JOB_DATE")
        ]


def scope(key, day):
    if key == "risk_parameters":
        return {"type": "GLOBAL"}
    return {"type": "DATE", "date": day}


def amount(value):
    return str(Decimal(value).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def credit(inputs):
    return {
        "credit_risk": amount(
            sum(inputs["credit_exposures"]) * Decimal(inputs["risk_parameters"]["creditFactor"])
        )
    }


def market(inputs):
    exposure = sum(abs(position) for position in inputs["market_positions"])
    return {"market_risk": amount(exposure * Decimal(inputs["risk_parameters"]["marketFactor"]))}


def liquidity(inputs):
    incoming, outgoing = inputs["cash_flows"]
    return {"liquidity_risk": amount(max(0, outgoing - incoming))}


def summary(inputs):
    return {"risk_summary": amount(sum(Decimal(value) for value in inputs.values()))}


def parameters(inputs):
    credit_pct, market_pct = inputs["parameter_inputs"]
    return {
        "risk_parameters": {
            "creditFactor": amount(Decimal(credit_pct) / 100),
            "marketFactor": amount(Decimal(market_pct) / 100),
        }
    }


def operational(inputs):
    return {"operational_risk": amount(sum(inputs["incident_losses"]))}


PROCESSES = {
    p.code: p
    for p in (
        Process(
            "operational-risk",
            "Операционный риск",
            ("incident_losses",),
            ("operational_risk",),
            operational,
        ),
        Process(
            "credit-risk",
            "Кредитный риск",
            ("credit_exposures", "risk_parameters"),
            ("credit_risk",),
            credit,
        ),
        Process(
            "market-risk",
            "Рыночный риск",
            ("market_positions", "risk_parameters"),
            ("market_risk",),
            market,
        ),
        Process(
            "liquidity-risk",
            "Риск ликвидности",
            ("cash_flows",),
            ("liquidity_risk",),
            liquidity,
        ),
        Process(
            "risk-summary",
            "Сводка рисков",
            ("credit_risk", "market_risk", "liquidity_risk"),
            ("risk_summary",),
            summary,
        ),
        Process(
            "refresh-parameters",
            "Обновление коэффициентов",
            ("parameter_inputs",),
            ("risk_parameters",),
            parameters,
        ),
    )
}
