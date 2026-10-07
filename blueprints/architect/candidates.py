# blueprints/architect/candidates.py
"""The drafted design (spec §3 step 5, lean form): one design on the topology the decision rules choose, drafted by the
model from the published library and the linter's own rules, and scored by code for the live trace. The alternatives
considered come from the decision rules (alternatives.py), not from a second draft."""
import re

from blueprints.architect import prompts
from blueprints.architect.capacity import LATENCY_TARGET_S
from blueprints.architect.llm import structured
from blueprints.cost import estimate
from blueprints.generate import Draft, _schema_and_example, enforce
from blueprints.linter import RULES as LINTER_RULES, lint

ALLOWED_WARNING = "topology-differs"          # a refine may keep a topology the rules allow but would not pick first


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    if not s:
        return "design"
    return s if s[0].isalpha() else ("d-" + s)[:41]


def brief(title: str, use_case: str, profile: dict) -> dict:
    return {"id": slug(title), "title": title[:120], "use_case": use_case[:80], "profile": profile}


def pin(body, brief_: dict, topology: str) -> dict:
    """Code owns the id, title, use case, profile, version, changelog and topology; the model owns the design."""
    pinned = enforce(body, brief_, 1, "architect draft", [])
    pinned["topology"] = topology
    return pinned


def draft(gateway, topology: str, brief_: dict, decision: dict, grounding: dict, previous: dict | None = None,
          instruction: str | None = None) -> dict:
    schema, example = _schema_and_example()
    payload = {"brief": brief_, "decision": {"topology": topology, "allowed_topologies": decision["allowed_topologies"],
                                            "mandatory": decision["mandatory"], "patterns": decision["patterns"]},
               "constraints": grounding["limits"], "references": grounding["references"],
               "patterns": grounding["patterns"], "linter_rules": LINTER_RULES, "schema": schema, "example": example}
    if previous:
        payload["previous_blueprint"] = previous
    if instruction:
        payload["instruction"] = instruction
    return pin(structured(gateway, "bulk", prompts.DRAFT_SYSTEM, payload, Draft).blueprint, brief_, topology)


def score(body: dict, context: dict, pricing: dict, now: float) -> dict:
    """What the live trace shows for the drafted design: linter findings, price, latency against the target."""
    result = lint(body, context)
    cost = estimate(body, pricing, now)
    target = LATENCY_TARGET_S[body["profile"]["latency"]]
    warnings = [v for v in result["violations"] if v["severity"] == "warning" and v["rule"] != ALLOWED_WARNING]
    return {"topology": body["topology"], "errors": result["errors"], "warnings": len(warnings),
            "monthly_usd": cost["monthly_usd"], "latency_s": cost["latency_s"], "latency_target_s": target,
            "over_budget": cost["over_budget"], "over_latency": cost["latency_s"] > target}
