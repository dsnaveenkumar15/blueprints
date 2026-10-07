# tests/test_blueprints_architect_graph.py
import pytest

pytest.importorskip("langgraph")

from blueprints.architect import candidates, review  # noqa: E402
from blueprints.architect.graph import Context, run_design  # noqa: E402
from blueprints.cost import load_pricing  # noqa: E402
from tests.blueprint_fixtures import CONTEXT, good, reply, routed_gateway  # noqa: E402
from tests.test_blueprints_architect_record import a_record  # noqa: E402

PASS = {**{a: 4 for a in review.AREAS}, "findings": [], "summary": "ready"}
FAIL = {**{a: 4 for a in review.AREAS}, "security": 3, "summary": "no",
        "findings": [{"area": "security", "blocking": True, "problem": "p", "fix": "f"}]}
BRIEF = candidates.brief("Refund support agent", "customer support", {**good()["profile"], "latency": "near_real_time"})


def draft():
    return reply({"blueprint": good()})


def broken():
    b = good()
    b["components"][3]["mitigations"] = []                # an unmitigated write: a linter error
    return reply({"blueprint": b})


def run(routes, **inputs):
    events = []
    gw = routed_gateway(routes)
    ctx = Context(gateway=gw, emit=lambda t, p: events.append((t, p)), lint_context=CONTEXT, pricing=load_pricing())
    result = run_design(ctx, {"brief": BRIEF, "assumptions": [], "previous": None, "instruction": None, **inputs})
    return result, events, gw


def clean_routes(**changes):
    routes = {"draft": [draft()], "record": [reply(a_record())], "review": [reply(PASS)]}
    routes.update(changes)
    return routes


def test_a_clean_design_passes_red_team_and_review():
    result, events, _ = run(clean_routes())
    assert result["verdict"] == "passed" and result["problems"] == []
    assert result["blueprint"]["id"] == "refund-support-agent" and result["capacity"]["scales"]
    assert result["alternatives"] and all(a["rejected_because"] for a in result["alternatives"])
    assert ("node", {"name": "review", "status": "start"}) in events


def test_linter_errors_go_through_the_fix_loop():
    result, events, _ = run(clean_routes(draft=[broken()], fix=[reply({"blueprint": good(), "record": a_record()})]))
    assert result["verdict"] == "passed"
    assert any(t == "node" and p == {"name": "fix", "status": "start"} for t, p in events)
    first = next(p["first"] for t, p in events if t == "check" and p["problems"])       # which rule and where, never the model's text
    assert first and all(": " in line for line in first)


def test_three_failed_reviews_end_not_ready_with_the_reason():
    result, _, _ = run(clean_routes(review=[reply(FAIL)] * 3, fix=[reply({"blueprint": good(), "record": a_record()})] * 3))
    assert result["verdict"] == "not_ready" and "three rounds" in result["reason"]
    assert result["review"]["scores"]["security"] == 3


def test_a_model_that_never_returns_json_ends_not_ready_without_crashing():
    result, _, _ = run({"draft": [reply("no"), reply("no")]})
    assert result["verdict"] == "not_ready" and "invalid output twice" in result["reason"] and result["blueprint"] is None


def test_running_out_of_paid_budget_stops_early_with_the_best_design_so_far():
    pricey = reply({"blueprint": good()})
    pricey.response_metadata = {"token_usage": {"cost": 0.5}, "model_provider": "p"}
    events = []
    import time
    from core.agent.gateway import Budget, Gateway
    from tests.blueprint_fixtures import RoutedModel
    model = RoutedModel({"draft": [pricey, reply({"blueprint": good()})]})
    gw = Gateway({"bulk": ["m"], "review": ["m"]}, lambda name: model, {"m": (0.0, 0.0)},
                 Budget(0.4, 10_000_000, 1, time.monotonic() + 60), lambda *a: None, lambda: None)
    ctx = Context(gateway=gw, emit=lambda t, p: events.append((t, p)), lint_context=CONTEXT, pricing=load_pricing())
    result = run_design(ctx, {"brief": BRIEF, "assumptions": [], "previous": None, "instruction": None})
    assert result["verdict"] == "not_ready" and "budget" in result["reason"] and result["blueprint"]["id"] == "refund-support-agent"


def test_a_refine_drafts_one_candidate_from_the_previous_design():
    result, _, gw = run(clean_routes(draft=[draft()]), previous=good(), instruction="budget $300 a month")
    first = next(m[1].content for m in gw.model.calls if "reference architectures" in m[0].content)
    assert "previous_blueprint" in first and "budget $300 a month" in first and result["verdict"] == "passed"