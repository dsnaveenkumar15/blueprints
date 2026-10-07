# tests/test_blueprints_architect_lean.py - the lean pipeline: one candidate, review that also attacks, partial fixes,
# rules in the prompts so the first draft is right more often.
import json

import pytest

pytest.importorskip("langgraph")

from blueprints.architect import candidates, fix, record, review  # noqa: E402
from blueprints.architect.graph import Context, run_design  # noqa: E402
from blueprints.cost import load_pricing  # noqa: E402
from tests.blueprint_fixtures import CONTEXT, good, reply, routed_gateway  # noqa: E402
from tests.test_blueprints_architect_record import a_record  # noqa: E402

SCORES = {a: 4 for a in review.AREAS}
BRIEF = candidates.brief("Refund support agent", "customer support", good()["profile"])


def run(routes, **inputs):
    gw = routed_gateway(routes)
    ctx = Context(gateway=gw, emit=lambda t, p: None, lint_context=CONTEXT, pricing=load_pricing())
    out = run_design(ctx, {"brief": BRIEF, "assumptions": [], "previous": None, "instruction": None, **inputs})
    return out, gw


def kinds(gw):
    return [next(k for k, marker in gw.model.MARKERS.items() if marker in m[0].content) for m in gw.model.calls]


def test_a_new_design_makes_exactly_three_model_calls_draft_record_review():
    result, gw = run({"draft": [reply({"blueprint": good()})], "record": [reply(a_record())],
                      "review": [reply({**SCORES, "findings": [], "attacks": [], "summary": "ready"})]})
    assert result["verdict"] == "passed" and kinds(gw) == ["draft", "record", "review"]
    assert result["variant"] is None and result["redteam"] == []
    assert result["alternatives"] and all(a["rejected_because"] for a in result["alternatives"])


def test_the_review_also_attacks_and_a_high_severity_attack_blocks_a_pass():
    attack = {"component": "orders-tool", "scenario": "refund loop", "severity": "high", "control": "cap refunds per hour"}
    ok = routed_gateway({"review": [reply({**SCORES, "findings": [], "attacks": [attack], "summary": "s"})]})
    assert not review.run(ok, good(), a_record(), {}, [])["passed"]
    low = {**attack, "severity": "low"}
    fine = routed_gateway({"review": [reply({**SCORES, "findings": [], "attacks": [low], "summary": "s"})]})
    result = review.run(fine, good(), a_record(), {}, [])
    assert result["passed"] and result["attacks"][0]["severity"] == "low"
    assert review.problems(review.run(routed_gateway({"review": [reply({**SCORES, "findings": [], "attacks": [attack], "summary": "s"})]}),
                                      good(), a_record(), {}, []))[0]["path"] == "components.orders-tool"


def test_a_fix_returns_only_what_changed_and_the_rest_is_kept():
    body = good()
    changed = {"observability": ["traces per request", "cost per request in dollars"]}
    gw = routed_gateway({"fix": [reply({"blueprint": changed, "record": {"rollback": "Route everything to people."}})]})
    brief = {"id": "refund-support", "title": "Refund support agent", "use_case": "customer support", "profile": body["profile"]}
    new_body, new_rec = fix.run(gw, body, a_record(), [{"path": "observability.0", "problem": "p", "fix": "f"}], brief, "router")
    assert new_body["observability"][1].startswith("cost per request") and new_body["components"] == body["components"]
    assert new_rec["rollback"] == "Route everything to people." and new_rec["runbook"] == a_record()["runbook"]


def test_a_fix_that_breaks_the_record_fails_the_step_not_the_run():
    from blueprints.architect.llm import StepFailed
    gw = routed_gateway({"fix": [reply({"blueprint": {}, "record": {"rollout": []}}), reply({"blueprint": {}, "record": {"rollout": []}})]})
    brief = {"id": "refund-support", "title": "t", "use_case": "u", "profile": good()["profile"]}
    with pytest.raises(StepFailed):
        fix.run(gw, good(), a_record(), [{"path": "x", "problem": "p", "fix": "f"}], brief, "router")


def test_the_draft_and_record_prompts_carry_the_rules_they_will_be_checked_against():
    result, gw = run({"draft": [reply({"blueprint": good()})], "record": [reply(a_record())],
                      "review": [reply({**SCORES, "findings": [], "attacks": [], "summary": "ready"})]})
    draft = json.loads(next(m for m in gw.model.calls if "reference architectures" in m[0].content)[1].content.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert "unmitigated-capability" in draft["linter_rules"] and "unknown-model" in draft["linter_rules"]
    rec = json.loads(next(m for m in gw.model.calls if "decision record" in m[0].content)[1].content.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert any("numbers" in r for r in rec["record_rules"]) and len(rec["record_rules"]) >= 5


def test_a_refine_still_drafts_from_the_previous_design():
    result, gw = run({"draft": [reply({"blueprint": good()})], "record": [reply(a_record())],
                      "review": [reply({**SCORES, "findings": [], "attacks": [], "summary": "ready"})]},
                     previous=good(), instruction="budget $300 a month")
    first = next(m[1].content for m in gw.model.calls if "reference architectures" in m[0].content)
    assert "previous_blueprint" in first and "budget $300 a month" in first
