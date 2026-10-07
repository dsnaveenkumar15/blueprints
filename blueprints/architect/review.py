# blueprints/architect/review.py
"""Architect review (spec §3 step 11) on the paid strong model, which is also the red team. It sees the design, the
record, the capacity numbers and the labelled assumptions, never the visitor's text. Pass: every area at least 4, no
blocking finding and no high or critical attack the design does not control."""
from typing import Literal

from pydantic import BaseModel, Field

from blueprints.architect import prompts
from blueprints.architect.llm import structured

AREAS = ("requirement_fit", "topology", "failure_handling", "security", "cost_and_scale", "evaluation", "observability", "rollout")
PASS_SCORE = 4
SIGNIFICANT = ("high", "critical")


class Finding(BaseModel):
    area: Literal[AREAS]
    blocking: bool
    problem: str = Field(min_length=1, max_length=400)
    fix: str = Field(min_length=1, max_length=400)


class Attack(BaseModel):
    component: str = Field(min_length=1, max_length=41)
    scenario: str = Field(min_length=1, max_length=500)
    severity: Literal["low", "medium", "high", "critical"]
    control: str = Field(min_length=1, max_length=400)


class Review(BaseModel):
    requirement_fit: int = Field(ge=1, le=5)
    topology: int = Field(ge=1, le=5)
    failure_handling: int = Field(ge=1, le=5)
    security: int = Field(ge=1, le=5)
    cost_and_scale: int = Field(ge=1, le=5)
    evaluation: int = Field(ge=1, le=5)
    observability: int = Field(ge=1, le=5)
    rollout: int = Field(ge=1, le=5)
    findings: list[Finding] = Field(default_factory=list, max_length=20)
    attacks: list[Attack] = Field(default_factory=list, max_length=12)
    summary: str = Field(min_length=1, max_length=600)


def run(gateway, body: dict, rec: dict, cap: dict, assumptions: list[str]) -> dict:
    payload = {"blueprint": body, "record": rec, "capacity": cap, "assumptions": assumptions}
    r = structured(gateway, "review", prompts.REVIEW_SYSTEM, payload, Review)
    scores = {a: getattr(r, a) for a in AREAS}
    findings, attacks = [f.model_dump() for f in r.findings], [a.model_dump() for a in r.attacks]
    return {"scores": scores, "findings": findings, "attacks": attacks, "summary": r.summary,
            "passed": all(s >= PASS_SCORE for s in scores.values()) and not any(f["blocking"] for f in findings)
            and not any(a["severity"] in SIGNIFICANT for a in attacks)}


def problems(result: dict) -> list[dict]:
    """What the fix step must address: blocking findings, findings in an area scored below the bar, significant attacks."""
    low = {a for a, s in result["scores"].items() if s < PASS_SCORE}
    found = [{"path": f["area"], "problem": f["problem"], "fix": f["fix"]} for f in result["findings"]
             if f["blocking"] or f["area"] in low]
    found += [{"path": f"components.{a['component']}", "problem": f"attack ({a['severity']}): {a['scenario']}", "fix": a["control"]}
              for a in result.get("attacks", []) if a["severity"] in SIGNIFICANT]
    return found or [{"path": a, "problem": f"{a} scored {result['scores'][a]} of 5", "fix": "raise it to production-ready"}
                     for a in sorted(low)]
