# blueprints/architect/fix.py
"""The fix step (spec §3 steps 9 and 11): the exact problems go back to the bulk model, which returns ONLY the top-level
fields it changed (a full rewrite of a design takes minutes and thousands of tokens). Code merges them, pins what it
owns and validates the record; a record that no longer validates fails the step, never the run."""
from pydantic import BaseModel, ValidationError

from blueprints.architect import prompts
from blueprints.architect.candidates import pin
from blueprints.architect.llm import StepFailed, structured
from blueprints.architect.record import Record
from blueprints.generate import _schema_and_example

MAX_PROBLEMS = 25


class Fixed(BaseModel):
    blueprint: dict = {}
    record: dict | None = None


def from_checks(checks: list[dict]) -> list[dict]:
    return [{"path": c["path"], "problem": f"{c['rule']}: {c['message']}", "fix": "change the design so this check passes"}
            for c in checks]


def run(gateway, body: dict, rec: dict | None, problems: list[dict], brief_: dict, topology: str) -> tuple[dict, dict]:
    schema, _ = _schema_and_example()
    payload = {"blueprint": body, "record": rec, "problems": problems[:MAX_PROBLEMS], "schema": schema}
    out = structured(gateway, "bulk", prompts.FIX_SYSTEM, payload, Fixed)
    try:
        merged_record = Record.model_validate({**(rec or {}), **(out.record or {})}).model_dump()
    except ValidationError:
        raise StepFailed("Record: the fix returned a record that does not validate") from None
    return pin({**body, **out.blueprint}, brief_, topology), merged_record
