# blueprints/architect/record.py
"""The decision record (spec §3 step 7): context, decision, measured alternatives, trade-offs, targets, scaling,
rollout, rollback, open risks and an on-call runbook; plus the cheaper variant, itself a blueprint the linter checks."""
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from blueprints.architect import prompts
from blueprints.architect.candidates import pin
from blueprints.architect.consistency import RECORD_RULES
from blueprints.architect.llm import structured

Line = Annotated[str, StringConstraints(min_length=1, max_length=600)]
RefId = Annotated[str, StringConstraints(min_length=1, max_length=41)]


class Alternative(BaseModel):
    topology: str = Field(max_length=40)
    summary: str = Field(max_length=600)
    rejected_because: str = Field(max_length=900)


class Targets(BaseModel):
    latency_p95_s: float = Field(gt=0, le=86400)
    availability_pct: float = Field(ge=90, le=100)
    quality: str = Field(min_length=1, max_length=400)


class Risk(BaseModel):
    text: Line
    failure_mode: RefId


class RunbookItem(BaseModel):
    failure_mode: RefId
    alert: Annotated[str, StringConstraints(min_length=1, max_length=400)]
    meaning: Line
    first_action: Line


class Record(BaseModel):
    context: str = Field(min_length=1, max_length=2500)
    decision: str = Field(min_length=1, max_length=1800)
    alternatives: list[Alternative] = Field(default_factory=list, max_length=3)
    trade_offs: list[Line] = Field(min_length=1, max_length=8)
    targets: Targets
    scaling: str = Field(min_length=1, max_length=1800)
    rollout: list[Line] = Field(min_length=2, max_length=6)
    rollback: str = Field(min_length=1, max_length=900)
    open_risks: list[Risk] = Field(default_factory=list, max_length=8)
    runbook: list[RunbookItem] = Field(min_length=1, max_length=10)


class Variant(BaseModel):
    blueprint: dict
    summary: str = Field(min_length=1, max_length=900)
    changes: list[Annotated[str, StringConstraints(min_length=1, max_length=400)]] = Field(min_length=1, max_length=6)


def write(gateway, body: dict, cap: dict, alternatives: list[dict], assumptions: list[str]) -> dict:
    payload = {"blueprint": body, "capacity": cap, "alternatives": alternatives, "assumptions": assumptions,
               "record_rules": RECORD_RULES}
    rec = structured(gateway, "bulk", prompts.RECORD_SYSTEM, payload, Record).model_dump()
    extra = [a for a in rec["alternatives"] if a["topology"] not in {c["topology"] for c in alternatives}][:1]
    rec["alternatives"] = [Alternative.model_validate(a).model_dump() for a in alternatives] + extra
    return rec


def variant(gateway, body: dict, grounding: dict, brief_: dict) -> dict:
    out = structured(gateway, "bulk", prompts.VARIANT_SYSTEM, {"blueprint": body, "constraints": grounding["limits"]}, Variant)
    lean = pin(out.blueprint, {**brief_, "id": (brief_["id"] + "-lean")[:41], "title": (brief_["title"] + " (lean)")[:120]},
               body["topology"])
    return {"blueprint": lean, "summary": out.summary, "changes": list(out.changes)}
