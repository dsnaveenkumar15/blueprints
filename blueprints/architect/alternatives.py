# blueprints/architect/alternatives.py
"""The alternatives considered, from the decision engine's own reasoning (no model call, no guessing): a topology the
rules ruled out is rejected with the rule that removed it; one that stayed allowed but lost is rejected with the rule
that chose the winner."""
from blueprints.decision import RULES

# which rule removes which topologies (mirrors blueprints.decision.decide)
REMOVED_BY = {
    "interactive-latency": ("orchestrator-workers", "supervisor", "evaluator-optimizer", "autonomous"),
    "high-risk-no-autonomy": ("autonomous",),
    "prefer-workflow": ("autonomous", "supervisor"),
    "autonomous-needs-consent": ("autonomous",),
}
CLOSEST = {
    "workflow": ["router", "evaluator-optimizer", "orchestrator-workers", "supervisor", "autonomous"],
    "router": ["workflow", "supervisor", "orchestrator-workers", "evaluator-optimizer", "autonomous"],
    "orchestrator-workers": ["supervisor", "workflow", "router", "evaluator-optimizer", "autonomous"],
    "supervisor": ["orchestrator-workers", "router", "workflow", "evaluator-optimizer", "autonomous"],
    "evaluator-optimizer": ["workflow", "router", "orchestrator-workers", "supervisor", "autonomous"],
    "autonomous": ["supervisor", "orchestrator-workers", "workflow", "router", "evaluator-optimizer"],
}
MAX_ALTERNATIVES = 3


def from_decision(decision: dict) -> list[dict]:
    chosen = decision["topology"]
    fired = {t["rule"] for t in decision["trace"]}
    winner = next((t for t in decision["trace"] if t["effect"] == chosen), None)
    out = []
    for topology in CLOSEST[chosen]:
        if topology in decision["allowed_topologies"]:
            why = (f"The decision rules chose {chosen}: {RULES[winner['rule']]}" if winner
                   else f"The decision rules chose {chosen} for these requirements.")
        else:
            rule = next((r for r, removed in REMOVED_BY.items() if r in fired and topology in removed), None)
            why = f"Ruled out: {RULES[rule]}" if rule else "Ruled out by the decision rules for these requirements."
        out.append({"topology": topology, "summary": f"The {topology} topology", "rejected_because": why})
    return out[:MAX_ALTERNATIVES]
