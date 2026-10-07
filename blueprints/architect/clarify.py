# blueprints/architect/clarify.py
"""Clarify (spec §3 step 2): the visitor's words become a requirements profile, then at most four multiple-choice
questions about the unknown fields, ranked by how much each answer changes the design. The model only reads the
description; code ranks, using the decision engine. Fields are tried in pairs, because some only matter together
(risk changes the controls only when a write capability is possible)."""
from pydantic import BaseModel, Field, ValidationError

from blueprints.architect import prompts
from blueprints.architect.llm import structured
from blueprints.decision import decide
from blueprints.models import Profile

MAX_QUESTIONS = 4
DEFAULTS = {"latency": "interactive", "volume_per_day": 1000, "risk": "medium", "data_sensitivity": "internal",
            "autonomy": "suggest", "budget_usd_month": 500, "capabilities": ["read"], "open_ended": False,
            "task_kinds": 1, "parallelizable": False, "knowledge": False, "quality_critical": False}
OPTIONS = {
    "latency": [("interactive", "A person waits for each answer"), ("near_real_time", "Within a minute or so"),
                ("batch", "Overnight or on a schedule")],
    "volume_per_day": [(100, "About 100 requests a day"), (1000, "About 1,000 a day"), (10000, "About 10,000 a day"),
                       (100000, "100,000 or more a day")],
    "risk": [("low", "Low: mistakes are cheap to undo"), ("medium", "Medium: mistakes cost time or money"),
             ("high", "High: mistakes hurt people, money or trust")],
    "data_sensitivity": [("public", "Public data only"), ("internal", "Internal business data"), ("pii", "Personal data"),
                         ("regulated", "Regulated data (health, finance)")],
    "autonomy": [("suggest", "It only suggests; people act"), ("act_with_approval", "It acts after a person approves"),
                 ("act_autonomously", "It acts on its own")],
    "budget_usd_month": [(100, "About $100 a month"), (500, "About $500 a month"), (2000, "About $2,000 a month"),
                         (10000, "$10,000 or more a month")],
    "capabilities": [(["read"], "Only reads data"), (["read", "write"], "Reads and changes records"),
                     (["read", "write", "payment"], "Reads, changes records and moves money"),
                     (["read", "network"], "Reads and calls outside services"), (["read", "exec"], "Reads and runs code")],
    "open_ended": [(False, "Fixed steps I can describe"), (True, "Open-ended: the steps depend on what it finds")],
    "task_kinds": [(1, "One kind of request"), (3, "A few kinds of request"), (10, "Many kinds of request")],
    "parallelizable": [(False, "One thing at a time"), (True, "Sub-tasks can run in parallel")],
    "knowledge": [(False, "No document lookup"), (True, "Answers come from documents or a knowledge base")],
    "quality_critical": [(False, "Good enough is fine"), (True, "Output must meet a strict quality bar")],
}
TEXT = {"latency": "How quickly must it answer?", "volume_per_day": "How many requests a day?",
        "risk": "What happens when it gets something wrong?", "data_sensitivity": "What data does it see?",
        "autonomy": "May it act on its own?", "budget_usd_month": "What may it cost per month?",
        "capabilities": "What may it do?", "open_ended": "Are the steps fixed?",
        "task_kinds": "How many kinds of request?", "parallelizable": "Can the work be split?",
        "knowledge": "Does it need documents?", "quality_critical": "How strict is the quality bar?"}
WHY = {"latency": "Interactive answers rule out agent loops.",
       "volume_per_day": "Volume drives cost, capacity and rate limiting.",
       "risk": "High risk with actions requires a human approval step.",
       "data_sensitivity": "Personal or regulated data needs input filtering and an audit log.",
       "autonomy": "Acting without approval changes the topology and its controls.",
       "budget_usd_month": "The design is priced against your budget.",
       "capabilities": "Each risky capability needs its own control.",
       "open_ended": "Open-ended work needs an agentic topology.",
       "task_kinds": "Several kinds of request are best routed.",
       "parallelizable": "Parallel sub-tasks change the topology.",
       "knowledge": "Document lookup adds a retrieval component.",
       "quality_critical": "A strict quality bar adds an evaluator loop."}
COST_FIELDS = ("volume_per_day", "budget_usd_month")


class Extracted(BaseModel):
    title: str = Field(min_length=1, max_length=120)
    use_case: str = Field(min_length=1, max_length=80)
    profile: dict = Field(default_factory=dict)
    stated: list[str] = Field(default_factory=list, max_length=12)


class Reprofiled(BaseModel):
    profile: dict


def merge(partial: dict, stated: list[str]) -> tuple[dict, list[str]]:
    """Stated values that are valid win; anything invalid or unstated takes the default and counts as unknown."""
    profile, known = dict(DEFAULTS), []
    for field in DEFAULTS:
        if field in stated and field in partial:
            try:
                Profile.model_validate({**profile, field: partial[field]})
            except ValidationError:
                continue
            profile[field] = partial[field]
            known.append(field)
    return Profile.model_validate(profile).model_dump(), known


def _outcome(profile: dict) -> tuple:
    d = decide(profile)
    return d["topology"], tuple(sorted(m["kind"] for m in d["mandatory"])), tuple(d["allowed_topologies"])


def impact(field: str, profile: dict, unknown: list[str]) -> int:
    """How many different designs this field's options lead to, at its most telling setting of one other unknown field."""
    best = 0
    for other in [None, *[u for u in unknown if u != field]]:
        settings = [profile] if other is None else [{**profile, other: v} for v, _ in OPTIONS[other]]
        for base in settings:
            best = max(best, len({_outcome({**base, field: v}) for v, _ in OPTIONS[field]}) - 1)
    return best + (1 if field in COST_FIELDS else 0)


def questions(profile: dict, known: list[str]) -> list[dict]:
    unknown = [f for f in DEFAULTS if f not in known]
    ranked = sorted(((impact(f, profile, unknown), i, f) for i, f in enumerate(DEFAULTS) if f in unknown),
                    key=lambda t: (-t[0], t[1]))
    out = []
    for score, _, field in ranked[:MAX_QUESTIONS]:
        if score <= 0:
            break
        opts = OPTIONS[field]
        suggested = next((i for i, (v, _) in enumerate(opts) if v == profile[field]), 0)
        out.append({"field": field, "text": TEXT[field], "why": WHY[field], "suggested": suggested,
                    "options": [{"index": i, "label": label} for i, (_, label) in enumerate(opts)]})
    return out


def label_of(field: str, value) -> str:
    return next((label for v, label in OPTIONS[field] if v == value), str(value))


def apply_answers(profile: dict, known: list[str], asked: list[dict], answers: dict) -> tuple[dict, list[str]]:
    asked_fields = {q["field"] for q in asked}
    updated, answered = dict(profile), []
    for field, index in answers.items():
        if field not in asked_fields or type(index) is not int or not 0 <= index < len(OPTIONS[field]):
            raise ValueError("Answer only the questions shown, with one of their options.")
        updated[field] = OPTIONS[field][index][0]
        answered.append(field)
    assumptions = [f'{TEXT[f].rstrip("?")}: assumed "{label_of(f, updated[f])}"'
                   for f in DEFAULTS if f not in known and f not in answered]
    return Profile.model_validate(updated).model_dump(), assumptions


def clarify(gateway, description: str) -> dict:
    allowed = {f: [v for v, _ in OPTIONS[f]] for f in DEFAULTS}
    ex = structured(gateway, "bulk", prompts.CLARIFY_SYSTEM, {"description": description, "profile_fields": allowed}, Extracted)
    profile, known = merge(ex.profile, ex.stated)
    return {"title": ex.title.strip(), "use_case": ex.use_case.strip(), "profile": profile, "known": known,
            "questions": questions(profile, known)}


def reprofile(gateway, profile: dict, instruction: str) -> dict:
    allowed = {f: [v for v, _ in OPTIONS[f]] for f in DEFAULTS}
    out = structured(gateway, "bulk", prompts.REPROFILE_SYSTEM,
                     {"profile": profile, "instruction": instruction, "profile_fields": allowed}, Reprofiled)
    merged, _ = merge({**profile, **out.profile}, list(DEFAULTS))
    return merged
