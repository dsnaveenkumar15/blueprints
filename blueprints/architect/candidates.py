# blueprints/architect/candidates.py
"""Two candidate designs (spec §3 step 5): one per topology, drafted by the model, scored by code. The decision
engine's topology is the primary; the closest allowed alternative competes. The loser becomes an alternative in the
decision record, rejected with the numbers code measured."""
import re

from blueprints.architect import prompts
from blueprints.architect.capacity import LATENCY_TARGET_S
from blueprints.architect.llm import structured
from blueprints.cost import estimate
from blueprints.generate import Draft, _schema_and_example, enforce
from blueprints.linter import lint
from blueprints.models import HIGH_RISK_CAPABILITIES

ALLOWED_WARNING = "topology-differs"          # expected for the alternative; never counted against it
CLOSEST = {
    "workflow": ["router", "evaluator-optimizer", "orchestrator-workers", "supervisor", "autonomous"],
    "router": ["workflow", "supervisor", "orchestrator-workers", "evaluator-optimizer", "autonomous"],
    "orchestrator-workers": ["supervisor", "workflow", "router", "evaluator-optimizer", "autonomous"],
    "supervisor": ["orchestrator-workers", "router", "workflow", "evaluator-optimizer", "autonomous"],
    "evaluator-optimizer": ["workflow", "router", "orchestrator-workers", "supervisor", "autonomous"],
    "autonomous": ["supervisor", "orchestrator-workers", "workflow", "router", "evaluator-optimizer"],
}
AGENCY = {"workflow": 0, "router": 1, "evaluator-optimizer": 2, "orchestrator-workers": 3, "supervisor": 3, "autonomous": 4}


def slug(title: str) -> str:
    s = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-")[:40].strip("-")
    if not s:
        return "design"
    return s if s[0].isalpha() else ("d-" + s)[:41]


def brief(title: str, use_case: str, profile: dict) -> dict:
    return {"id": slug(title), "title": title[:120], "use_case": use_case[:80], "profile": profile}


def topologies(decision: dict) -> list[str]:
    primary = decision["topology"]
    alt = next((t for t in CLOSEST[primary] if t in decision["allowed_topologies"]), None)
    return [primary] + ([alt] if alt else [])


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
               "patterns": grounding["patterns"], "schema": schema, "example": example}
    if previous:
        payload["previous_blueprint"] = previous
    if instruction:
        payload["instruction"] = instruction
    return pin(structured(gateway, "bulk", prompts.DRAFT_SYSTEM, payload, Draft).blueprint, brief_, topology)


def score(body: dict, decision: dict, context: dict, pricing: dict, now: float) -> dict:
    result = lint(body, context)
    cost = estimate(body, pricing, now)
    target = LATENCY_TARGET_S[body["profile"]["latency"]]
    warnings = [v for v in result["violations"] if v["severity"] == "warning" and v["rule"] != ALLOWED_WARNING]
    risky = sum(len(set(c.get("capabilities") or []) & set(HIGH_RISK_CAPABILITIES))
                for c in body.get("components") or [] if isinstance(c, dict))
    return {"topology": body["topology"], "errors": result["errors"], "warnings": len(warnings),
            "monthly_usd": cost["monthly_usd"], "latency_s": cost["latency_s"], "latency_target_s": target,
            "over_budget": cost["over_budget"], "over_latency": cost["latency_s"] > target,
            "risky_capabilities": risky, "agency": AGENCY[body["topology"]], "primary": body["topology"] == decision["topology"]}


def _key(s: dict) -> tuple:
    return (s["errors"], s["warnings"], s["over_budget"], s["over_latency"],
            s["monthly_usd"] if s["monthly_usd"] is not None else float("inf"), s["risky_capabilities"], s["agency"],
            not s["primary"])


def choose(scored: list[dict]) -> int:
    return min(range(len(scored)), key=lambda i: _key(scored[i]))


def penalties(winner: dict, loser: dict) -> list[str]:
    out = []
    if loser["errors"] > winner["errors"]:
        out.append(f"{loser['errors']} linter errors against {winner['errors']}")
    if winner["monthly_usd"] and loser["monthly_usd"] and loser["monthly_usd"] > winner["monthly_usd"]:
        out.append(f"{loser['monthly_usd'] / winner['monthly_usd']:.1f}x the monthly cost "
                   f"(${loser['monthly_usd']:,.0f} against ${winner['monthly_usd']:,.0f})")
    if winner["latency_s"] and loser["latency_s"] > winner["latency_s"]:
        out.append(f"{loser['latency_s'] / winner['latency_s'] - 1:.0%} slower ({loser['latency_s']:g} s against "
                   f"{winner['latency_s']:g} s)")
    if loser["risky_capabilities"] > winner["risky_capabilities"]:
        out.append(f"{loser['risky_capabilities'] - winner['risky_capabilities']} more high-risk capabilities")
    if loser["agency"] > winner["agency"]:
        out.append("more autonomy than the requirements need")
    if loser["over_budget"] and not winner["over_budget"]:
        out.append("over the monthly budget")
    if loser["over_latency"] and not winner["over_latency"]:
        out.append(f"misses the {loser['latency_target_s']:g} s latency target")
    return out or ["no measurable advantage over the chosen design"]
