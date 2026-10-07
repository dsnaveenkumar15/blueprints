# blueprints/architect/redteam.py
"""Red team (spec §3 step 10): an adversarial pass on the free model before the paid review. Findings of medium
severity or above go back through the fix loop; the whole log goes to the reviewer."""
from typing import Literal

from pydantic import BaseModel, Field

from blueprints.architect import prompts
from blueprints.architect.llm import structured

SIGNIFICANT = ("medium", "high", "critical")


class Attack(BaseModel):
    component: str = Field(min_length=1, max_length=41)
    scenario: str = Field(min_length=1, max_length=400)
    severity: Literal["low", "medium", "high", "critical"]
    control: str = Field(min_length=1, max_length=300)


class RedTeam(BaseModel):
    findings: list[Attack] = Field(default_factory=list, max_length=12)


def run(gateway, body: dict) -> list[dict]:
    return [f.model_dump() for f in structured(gateway, "bulk", prompts.REDTEAM_SYSTEM, {"blueprint": body}, RedTeam).findings]


def problems(findings: list[dict]) -> list[dict]:
    return [{"path": f"components.{f['component']}", "problem": f"red team ({f['severity']}): {f['scenario']}", "fix": f["control"]}
            for f in findings if f["severity"] in SIGNIFICANT]
