# tests/test_blueprints_architect_graph.py
import pytest

pytest.importorskip("langgraph")

from blueprints.architect import candidates, review  # noqa: E402
from blueprints.architect.graph import Context, run_design  # noqa: E402
from blueprints.cost import load_pricing  # noqa: E402
from tests.blueprint_fixtures import CONTEXT, good, reply, scripted_gateway  # noqa: E402
from tests.test_blueprints_architect_record import a_record, cheaper  # noqa: E402

PASS = {**{a: 4 for a in review.AREAS}, "findings": [], "summary": "ready"}
FAIL = {**{a: 4 for a in review.AREAS}, "security": 3, "summary": "no",
        "findings": [{"area": "security", "blocking": True, "problem": "p", "fix": "f"}]}
BRIEF = candidates.brief("Refund support agent", "customer support", good()["profile"])


def draft():
    return reply({"blueprint": good()})


def broken():
    b = good()
    b["components"][3]["mitigations"] = []                # an unmitigated write: a linter error
    return reply({"blueprint": b})


def variant():
    return reply({"blueprint": cheaper(good()), "summary": "smaller answer prompts", "changes": ["fewer tokens"]})


def run(script, **inputs):
    events = []
    gw = scripted_gateway(script)
    ctx = Context(gateway=gw, emit=lambda t, p: events.append((t, p)), lint_context=CONTEXT, pricing=load_pricing())
    result = run_design(ctx, {"brief": BRIEF, "assumptions": [], "previous": None, "instruction": None, **inputs})
    return result, events, gw


def test_a_clean_design_passes_red_team_and_review():
    result, events, _ = run({"free": [draft(), draft(), reply(a_record()), variant(), reply({"findings": []})],
                             "strong": [reply(PASS)]})
    assert result["verdict"] == "passed" and result["problems"] == []
    assert result["blueprint"]["id"] == "refund-support-agent" and result["capacity"]["scales"]
    alt = result["alternatives"][0]                      # the loser, rejected with a reason code measured
    assert alt["topology"] in ("workflow", "router") and alt["rejected_because"]
    assert ("node", {"name": "review", "status": "start"}) in events


def test_linter_errors_go_through_the_fix_loop():
    result, events, _ = run({"free": [broken(), broken(), reply(a_record()), variant(),
                                      reply({"blueprint": good(), "record": a_record()}), reply({"findings": []})],
                             "strong": [reply(PASS)]})
    assert result["verdict"] == "passed"
    assert any(t == "node" and p == {"name": "fix", "status": "start"} for t, p in events)


def test_a_significant_red_team_finding_is_fixed_before_review():
    finding = {"findings": [{"component": "orders-tool", "scenario": "refund loop", "severity": "high", "control": "cap refunds"}]}
    result, _, gw = run({"free": [draft(), draft(), reply(a_record()), variant(), reply(finding),
                                  reply({"blueprint": good(), "record": a_record()})],
                         "strong": [reply(PASS)]})
    assert result["verdict"] == "passed" and result["redteam"][0]["severity"] == "high"
    fix_prompt = gw.factory("free").calls[5][1].content
    assert "refund loop" in fix_prompt


def test_two_failed_reviews_end_not_ready_with_the_reason():
    result, _, _ = run({"free": [draft(), draft(), reply(a_record()), variant(), reply({"findings": []}),
                                 reply({"blueprint": good(), "record": a_record()})],
                        "strong": [reply(FAIL), reply(FAIL)]})
    assert result["verdict"] == "not_ready" and "two rounds" in result["reason"]
    assert result["review"]["scores"]["security"] == 3


def test_a_model_that_never_returns_json_ends_not_ready_without_crashing():
    result, _, _ = run({"free": [reply("no"), reply("no"), reply("still no"), reply("still no")]})
    assert result["verdict"] == "not_ready" and "invalid output twice" in result["reason"] and result["blueprint"] is None


def test_running_out_of_paid_budget_stops_early_with_the_best_design_so_far():
    pricey = reply({"blueprint": good()})
    pricey.response_metadata = {"token_usage": {"cost": 0.5}, "model_provider": "p"}
    events = []
    gw = scripted_gateway({"paid": [pricey]}, max_usd=0.4)
    ctx = Context(gateway=gw, emit=lambda t, p: events.append((t, p)), lint_context=CONTEXT, pricing=load_pricing())
    result = run_design(ctx, {"brief": BRIEF, "assumptions": [], "previous": None, "instruction": None})
    assert result["verdict"] == "not_ready" and "budget" in result["reason"] and result["blueprint"]["id"] == "refund-support-agent"


def test_a_refine_drafts_one_candidate_from_the_previous_design():
    result, _, gw = run({"free": [draft(), reply(a_record()), variant(), reply({"findings": []})], "strong": [reply(PASS)]},
                        previous=good(), instruction="budget $300 a month")
    first = gw.factory("free").calls[0][1].content
    assert "previous_blueprint" in first and "budget $300 a month" in first and result["verdict"] == "passed"
