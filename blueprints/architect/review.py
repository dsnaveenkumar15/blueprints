# blueprints/architect/review.py
"""Architect review (spec §3 step 11) on the paid strong model, which is also the red team. It sees the design, the
record, the capacity numbers and the labelled assumptions, never the visitor's text. Pass: every area at least 4, no
blocking finding and no high or critical attack the design does not control."""
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from blueprints.architect import prompts
from blueprints.architect.llm import clip, clip_list, structured

AREAS = ("requirement_fit", "topology", "failure_handling", "security", "cost_and_scale", "evaluation", "observability", "rollout")
PASS_SCORE = 4
SIGNIFICANT = ("high", "critical")          # attacks the loop tries to close; only an uncontrolled critical one blocks a pass


class Finding(BaseModel):
    area: Literal[AREAS]
    blocking: bool
    problem: Annotated[str, clip(400), Field(min_length=1)]
    fix: Annotated[str, clip(400), Field(min_length=1)]


class Attack(BaseModel):
    component: Annotated[str, clip(41), Field(min_length=1)]
    scenario: Annotated[str, clip(500), Field(min_length=1)]
    severity: Literal["low", "medium", "high", "critical"]
    control: Annotated[str, clip(400), Field(min_length=1)]
    controlled: bool = False        # True only when the design as written already contains that control; unknown counts as not


class Review(BaseModel):
    requirement_fit: int = Field(ge=1, le=5)
    topology: int = Field(ge=1, le=5)
    failure_handling: int = Field(ge=1, le=5)
    security: int = Field(ge=1, le=5)
    cost_and_scale: int = Field(ge=1, le=5)
    evaluation: int = Field(ge=1, le=5)
    observability: int = Field(ge=1, le=5)
    rollout: int = Field(ge=1, le=5)
    findings: Annotated[list[Finding], clip_list(20)] = Field(default_factory=list)
    attacks: Annotated[list[Attack], clip_list(12)] = Field(default_factory=list)
    summary: Annotated[str, clip(600), Field(min_length=1)]


def open_attack(a: dict) -> bool:
    """A high or critical attack the design does not already control: the only kind that blocks a pass."""
    return a["severity"] in SIGNIFICANT and not a.get("controlled")


def run(gateway, body: dict, rec: dict, cap: dict, assumptions: list[str]) -> dict:
    payload = {"blueprint": body, "record": rec, "capacity": cap, "assumptions": assumptions}
    r = structured(gateway, "review", prompts.REVIEW_SYSTEM, payload, Review)
    scores = {a: getattr(r, a) for a in AREAS}
    findings, attacks = [f.model_dump() for f in r.findings], [a.model_dump() for a in r.attacks]
    residual = [a for a in attacks if open_attack(a)]
    passed = (all(s >= PASS_SCORE for s in scores.values()) and not any(f["blocking"] for f in findings)
              and not any(a["severity"] == "critical" for a in residual))
    # `clean` also has no high attack left open; a pass that is not clean ships with its residual risks listed
    return {"scores": scores, "findings": findings, "attacks": attacks, "summary": r.summary, "passed": passed,
            "clean": passed and not residual, "residual": residual}


def problems(result: dict) -> list[dict]:
    """What the fix step must address: blocking findings, findings in an area scored below the bar, significant attacks."""
    low = {a for a, s in result["scores"].items() if s < PASS_SCORE}
    found = [{"path": f["area"], "problem": f["problem"], "fix": f["fix"]} for f in result["findings"]
             if f["blocking"] or f["area"] in low]
    found += [{"path": f"components.{a['component']}", "problem": f"attack ({a['severity']}): {a['scenario']}", "fix": a["control"]}
              for a in result.get("attacks", []) if open_attack(a)]
    return found or [{"path": a, "problem": f"{a} scored {result['scores'][a]} of 5", "fix": "raise it to production-ready"}
                     for a in sorted(low)]
