# blueprints/architect/fix.py
"""The fix step (spec §3 steps 9 and 11): the exact problems go back to the bulk model, which returns ONLY the top-level
fields it changed (a full rewrite of a design takes minutes and thousands of tokens). Code merges them, pins what it
owns and validates the record. A record that does not validate after the merge gets one repair (the model is told which
fields failed); a second failure fails the step, never the run."""
from pydantic import BaseModel, ValidationError

from blueprints.architect import prompts
from blueprints.architect.candidates import pin
from blueprints.architect.llm import StepFailed, structured
from blueprints.architect.record import Record
from blueprints.generate import _schema_and_example
from blueprints.linter import RULES as LINTER_RULES

MAX_PROBLEMS = 25


class Fixed(BaseModel):
    blueprint: dict = {}
    record: dict | None = None


def from_checks(checks: list[dict]) -> list[dict]:
    return [{"path": c["path"], "problem": f"{c['rule']}: {c['message']}", "fix": "change the design so this check passes"}
            for c in checks]


def _problems_of(exc: ValidationError) -> str:
    return "; ".join(f"{'.'.join(str(p) for p in e['loc']) or 'record'}: {e['msg']}" for e in exc.errors()[:6])


def run(gateway, body: dict, rec: dict | None, problems: list[dict], brief_: dict, topology: str,
        limits: dict | None = None) -> tuple[dict, dict]:
    schema, _ = _schema_and_example()
    payload = {"blueprint": body, "record": rec, "problems": problems[:MAX_PROBLEMS], "schema": schema,
               "constraints": limits or {}, "linter_rules": LINTER_RULES}
    last = ""
    for attempt in (1, 2):
        out = structured(gateway, "bulk", prompts.FIX_SYSTEM, payload, Fixed)
        try:
            merged = Record.model_validate({**(rec or {}), **(out.record or {})}).model_dump()
            return pin({**body, **out.blueprint}, brief_, topology), merged
        except ValidationError as exc:
            last = _problems_of(exc)
            payload = {**payload, "your_last_reply": f"the record you returned does not validate: {last}. Return the corrected changed fields."}
    raise StepFailed(f"Record: the fix returned a record that does not validate ({last[:160]})")
