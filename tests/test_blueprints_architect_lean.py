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
BRIEF = candidates.brief("Refund support agent", "customer support", {**good()["profile"], "latency": "near_real_time"})


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


def test_the_review_also_attacks_and_a_critical_uncontrolled_attack_blocks_a_pass():
    attack = {"component": "orders-tool", "scenario": "refund loop", "severity": "critical", "control": "cap refunds per hour"}
    bad = review.run(routed_gateway({"review": [reply({**SCORES, "findings": [], "attacks": [attack], "summary": "s"})]}),
                     good(), a_record(), {}, [])
    assert not bad["passed"] and review.problems(bad)[0]["path"] == "components.orders-tool"
    low = {**attack, "severity": "low"}
    fine = review.run(routed_gateway({"review": [reply({**SCORES, "findings": [], "attacks": [low], "summary": "s"})]}),
                      good(), a_record(), {}, [])
    assert fine["passed"] and fine["clean"] and fine["attacks"][0]["severity"] == "low" and review.problems(fine) == []


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


def test_a_fix_whose_record_does_not_validate_gets_one_repair_before_failing():
    bad = reply({"blueprint": {}, "record": {"rollout": []}})                      # fewer than two rollout steps
    ok = reply({"blueprint": {}, "record": {"rollout": ["shadow", "canary"]}})
    gw = routed_gateway({"fix": [bad, ok]})
    brief = {"id": "refund-support", "title": "t", "use_case": "u", "profile": good()["profile"]}
    body, rec = fix.run(gw, good(), a_record(), [{"path": "x", "problem": "p", "fix": "f"}], brief, "router")
    assert rec["rollout"] == ["shadow", "canary"]
    second = next(m for m in gw.model.calls[1:] if "You fix a design" in m[0].content)[1].content
    assert "rollout" in second and "does not validate" in second


def test_a_run_that_stops_says_which_step_failed():
    from blueprints.architect.llm import StepFailed
    result, _ = run({"draft": [reply({"blueprint": good()})], "record": [reply(a_record())],
                     "review": [reply("not json"), reply("still not json")]})
    assert result["verdict"] == "not_ready" and "Review" in result["reason"]


def test_the_fix_step_is_given_the_allowed_parts_and_the_linter_rules_so_it_cannot_invent_a_tool():
    gw = routed_gateway({"fix": [reply({"blueprint": {}, "record": {}})]})
    brief = {"id": "refund-support", "title": "t", "use_case": "u", "profile": good()["profile"]}
    limits = {"allowed_models": ["claude-haiku-4-5"], "allowed_tools": [], "allowed_frameworks": []}
    fix.run(gw, good(), a_record(), [{"path": "x", "problem": "p", "fix": "f"}], brief, "router", limits)
    sent = json.loads(gw.model.calls[0][1].content.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert sent["constraints"]["allowed_tools"] == [] and "tool-not-eligible" in sent["linter_rules"]
    assert "never add a tool" in gw.model.calls[0][0].content.lower()


def test_a_high_attack_the_design_already_controls_is_clean_and_an_open_one_is_a_residual_risk_to_fix():
    controlled = {"component": "kb", "scenario": "poisoned article", "severity": "high", "control": "second reviewer plus canary set", "controlled": True}
    open_attack = {**controlled, "controlled": False}
    ok = review.run(routed_gateway({"review": [reply({**SCORES, "findings": [], "attacks": [controlled], "summary": "s"})]}),
                    good(), a_record(), {}, [])
    assert ok["passed"] and ok["clean"] and review.problems(ok) == []
    left = review.run(routed_gateway({"review": [reply({**SCORES, "findings": [], "attacks": [open_attack], "summary": "s"})]}),
                      good(), a_record(), {}, [])
    assert left["passed"] and not left["clean"] and [p["path"] for p in review.problems(left)] == ["components.kb"]
    unknown = {k: v for k, v in controlled.items() if k != "controlled"}                      # no answer: treated as not controlled
    assert not review.run(routed_gateway({"review": [reply({**SCORES, "findings": [], "attacks": [unknown], "summary": "s"})]}),
                          good(), a_record(), {}, [])["clean"]


def test_three_review_rounds_are_allowed_before_giving_up():
    fail = reply({**SCORES, "security": 3, "findings": [{"area": "security", "blocking": True, "problem": "p", "fix": "f"}],
                  "attacks": [], "summary": "no"})
    fixed = reply({"blueprint": good(), "record": a_record()})
    result, gw = run({"draft": [reply({"blueprint": good()})], "record": [reply(a_record())], "fix": [fixed, fixed, fixed],
                      "review": [fail, fail, reply({**SCORES, "findings": [], "attacks": [], "summary": "ok"})]})
    assert result["verdict"] == "passed" and kinds(gw).count("review") == 3
    result, gw = run({"draft": [reply({"blueprint": good()})], "record": [reply(a_record())], "fix": [fixed, fixed, fixed],
                      "review": [fail, fail, fail]})
    assert result["verdict"] == "not_ready" and "three rounds" in result["reason"]


def test_a_critical_uncontrolled_attack_blocks_but_a_high_one_passes_as_a_residual_risk():
    base = {"component": "kb", "scenario": "s", "control": "c", "controlled": False}
    critical = review.run(routed_gateway({"review": [reply({**SCORES, "findings": [], "attacks": [{**base, "severity": "critical"}], "summary": "s"})]}),
                          good(), a_record(), {}, [])
    assert not critical["passed"]
    high = review.run(routed_gateway({"review": [reply({**SCORES, "findings": [], "attacks": [{**base, "severity": "high"}], "summary": "s"})]}),
                      good(), a_record(), {}, [])
    assert high["passed"] and not high["clean"] and [a["severity"] for a in high["residual"]] == ["high"]
    clean = review.run(routed_gateway({"review": [reply({**SCORES, "findings": [], "attacks": [{**base, "severity": "high", "controlled": True}], "summary": "s"})]}),
                       good(), a_record(), {}, [])
    assert clean["passed"] and clean["clean"] and clean["residual"] == []


def test_the_loop_tries_to_close_a_high_attack_and_passes_with_it_listed_when_it_cannot():
    high = {"component": "orders-tool", "scenario": "console bypass", "severity": "high", "control": "network allow-list", "controlled": False}
    open_review = reply({**SCORES, "findings": [], "attacks": [high], "summary": "open"})
    fixed = reply({"blueprint": good(), "record": a_record()})
    result, gw = run({"draft": [reply({"blueprint": good()})], "record": [reply(a_record())], "fix": [fixed] * 3,
                      "review": [open_review] * 3})
    assert result["verdict"] == "passed" and kinds(gw).count("review") == 3 and kinds(gw).count("fix") == 2
    assert [a["scenario"] for a in result["residual"]] == ["console bypass"]
    closed = reply({**SCORES, "findings": [], "attacks": [{**high, "controlled": True}], "summary": "closed"})
    result, gw = run({"draft": [reply({"blueprint": good()})], "record": [reply(a_record())], "fix": [fixed] * 3,
                      "review": [open_review, closed]})
    assert result["verdict"] == "passed" and kinds(gw).count("review") == 2 and result["residual"] == []


def test_a_design_that_passed_review_is_kept_when_a_later_fix_makes_it_worse():
    high = {"component": "orders-tool", "scenario": "console bypass", "severity": "high", "control": "network allow-list", "controlled": False}
    passed_with_risk = reply({**SCORES, "findings": [], "attacks": [high], "summary": "ok but one risk"})
    worse = reply({**SCORES, "security": 3, "findings": [{"area": "security", "blocking": True, "problem": "p", "fix": "f"}],
                   "attacks": [], "summary": "regressed"})
    changed = reply({"blueprint": {"deployment": {"runtime": "CHANGED BY A LATER FIX", "notes": ""}}, "record": {}})
    result, gw = run({"draft": [reply({"blueprint": good()})], "record": [reply(a_record())], "fix": [changed, changed],
                      "review": [passed_with_risk, worse, worse]})
    assert result["verdict"] == "passed" and "on round 1" in result["reason"] and "did not improve" in result["reason"]
    assert result["blueprint"]["deployment"]["runtime"] == good()["deployment"]["runtime"]      # the version that was reviewed and passed
    assert result["review"]["summary"] == "ok but one risk" and [a["scenario"] for a in result["residual"]] == ["console bypass"]


def test_a_design_that_never_passed_review_stays_not_ready():
    worse = reply({**SCORES, "security": 3, "findings": [{"area": "security", "blocking": True, "problem": "p", "fix": "f"}],
                   "attacks": [], "summary": "no"})
    fixed = reply({"blueprint": good(), "record": a_record()})
    result, _ = run({"draft": [reply({"blueprint": good()})], "record": [reply(a_record())], "fix": [fixed] * 3, "review": [worse] * 3})
    assert result["verdict"] == "not_ready" and "three rounds" in result["reason"]
