# blueprints/architect/review.py
"""Architect review (spec §3 step 11) on the paid strong model. It sees the design, the capacity numbers, the red-team
log and the labelled assumptions, never the visitor's text. Pass: every area at least 4 and no blocking finding."""
from typing import Literal

from pydantic import BaseModel, Field

from blueprints.architect import prompts
from blueprints.architect.llm import structured

AREAS = ("requirement_fit", "topology", "failure_handling", "security", "cost_and_scale", "evaluation", "observability", "rollout")
PASS_SCORE = 4


class Finding(BaseModel):
    area: Literal[AREAS]
    blocking: bool
    problem: str = Field(min_length=1, max_length=400)
    fix: str = Field(min_length=1, max_length=400)


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
    summary: str = Field(min_length=1, max_length=600)


def run(gateway, body: dict, rec: dict, cap: dict, redteam_findings: list[dict], assumptions: list[str]) -> dict:
    payload = {"blueprint": body, "record": rec, "capacity": cap, "redteam": redteam_findings, "assumptions": assumptions}
    r = structured(gateway, "review", prompts.REVIEW_SYSTEM, payload, Review)
    scores = {a: getattr(r, a) for a in AREAS}
    findings = [f.model_dump() for f in r.findings]
    return {"scores": scores, "findings": findings, "summary": r.summary,
            "passed": all(s >= PASS_SCORE for s in scores.values()) and not any(f["blocking"] for f in findings)}


def problems(result: dict) -> list[dict]:
    """What the fix step must address: blocking findings, and every finding in an area scored below the bar."""
    low = {a for a, s in result["scores"].items() if s < PASS_SCORE}
    found = [{"path": f["area"], "problem": f["problem"], "fix": f["fix"]} for f in result["findings"]
             if f["blocking"] or f["area"] in low]
    return found or [{"path": a, "problem": f"{a} scored {result['scores'][a]} of 5", "fix": "raise it to production-ready"}
                     for a in sorted(low)]
