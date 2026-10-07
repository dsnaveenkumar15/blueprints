# blueprints/architect/fix.py
"""The fix step (spec §3 steps 9 and 11): the exact problems go back to the bulk model, which returns the blueprint, the
record and (when given) the variant. Code pins what it owns and validates the record."""
from pydantic import BaseModel

from blueprints.architect import prompts
from blueprints.architect.candidates import pin
from blueprints.architect.llm import structured
from blueprints.architect.record import Record
from blueprints.generate import _schema_and_example

MAX_PROBLEMS = 25


class Fixed(BaseModel):
    blueprint: dict
    record: Record
    variant: dict | None = None


def from_checks(checks: list[dict]) -> list[dict]:
    return [{"path": c["path"], "problem": f"{c['rule']}: {c['message']}", "fix": "change the design so this check passes"}
            for c in checks]


def run(gateway, body: dict, rec: dict | None, variant: dict | None, problems: list[dict], brief_: dict,
        topology: str) -> tuple[dict, dict, dict | None]:
    """`variant` is the cheaper variant's blueprint (or None). Returns (blueprint, record, variant blueprint or None)."""
    schema, _ = _schema_and_example()
    payload = {"blueprint": body, "record": rec, "problems": problems[:MAX_PROBLEMS], "schema": schema}
    if variant:
        payload["variant"] = variant
    out = structured(gateway, "bulk", prompts.FIX_SYSTEM, payload, Fixed)
    lean = None
    if out.variant and variant:
        lean = pin(out.variant, {**brief_, "id": variant["id"], "title": variant["title"]}, topology)
    return pin(out.blueprint, brief_, topology), out.record.model_dump(), lean or variant
