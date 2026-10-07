# blueprints/architect/record.py
"""The decision record (spec §3 step 7): context, decision, measured alternatives, trade-offs, targets, scaling,
rollout, rollback, open risks and an on-call runbook; plus the cheaper variant, itself a blueprint the linter checks."""
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from blueprints.architect import prompts
from blueprints.architect.candidates import pin
from blueprints.architect.consistency import RECORD_RULES
from blueprints.architect.llm import clip, clip_list, structured

Line = Annotated[str, clip(600), StringConstraints(min_length=1)]
RefId = Annotated[str, clip(41), StringConstraints(min_length=1)]


class Alternative(BaseModel):
    topology: Annotated[str, clip(40)]
    summary: Annotated[str, clip(600)]
    rejected_because: Annotated[str, clip(900)]


class Targets(BaseModel):
    latency_p95_s: float = Field(gt=0, le=86400)
    availability_pct: float = Field(ge=90, le=100)
    quality: Annotated[str, clip(400), Field(min_length=1)]


class Risk(BaseModel):
    text: Line
    failure_mode: RefId


class RunbookItem(BaseModel):
    failure_mode: RefId
    alert: Annotated[str, clip(400), StringConstraints(min_length=1)]
    meaning: Line
    first_action: Line


class Record(BaseModel):
    context: Annotated[str, clip(2500), Field(min_length=1)]
    decision: Annotated[str, clip(1800), Field(min_length=1)]
    alternatives: Annotated[list[Alternative], clip_list(3)] = Field(default_factory=list)
    trade_offs: Annotated[list[Line], clip_list(8)] = Field(min_length=1)
    targets: Targets
    scaling: Annotated[str, clip(1800), Field(min_length=1)]
    rollout: Annotated[list[Line], clip_list(6)] = Field(min_length=2)
    rollback: Annotated[str, clip(900), Field(min_length=1)]
    open_risks: Annotated[list[Risk], clip_list(8)] = Field(default_factory=list)
    runbook: Annotated[list[RunbookItem], clip_list(10)] = Field(min_length=1)


class Variant(BaseModel):
    blueprint: dict
    summary: Annotated[str, clip(900), Field(min_length=1)]
    changes: Annotated[list[Annotated[str, clip(400), StringConstraints(min_length=1)]], clip_list(6)] = Field(min_length=1)


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
