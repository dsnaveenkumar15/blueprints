# tests/test_blueprints_architect_record.py
import copy

import pytest

pytest.importorskip("langchain_core")

from blueprints.architect import consistency, record  # noqa: E402
from blueprints.architect.capacity import capacity  # noqa: E402
from blueprints.cost import load_pricing  # noqa: E402
from tests.blueprint_fixtures import CONTEXT, good, reply, scripted_gateway  # noqa: E402

NOW = 1_790_877_600.0


def a_record(**changes) -> dict:
    r = {"context": "Support team answers order questions.", "decision": "A router separates refunds from questions.",
         "alternatives": [], "trade_offs": ["A router adds one cheap call."],
         "targets": {"latency_p95_s": 8.0, "availability_pct": 99.5, "quality": "routing accuracy at least 0.95"},
         "scaling": "Add answer workers horizontally; cache policy articles.",
         "rollout": ["shadow on past tickets", "canary at five percent", "full rollout"],
         "rollback": "Switch traffic back to the human queue.",
         "open_risks": [{"text": "Policy drift", "failure_mode": "stale-policy"}],
         "runbook": [{"failure_mode": "wrong-route", "alert": "routing accuracy below 0.9", "meaning": "refunds miss the gate",
                      "first_action": "pause auto-routing"}]}
    r.update(changes)
    return record.Record.model_validate(r).model_dump()


def cheaper(body):
    v = copy.deepcopy(body)
    v["flow"][1]["input_tokens"] = 1200
    return v


def check(body, rec, variant=None):
    body = {**body, "profile": {**body["profile"], "latency": "near_real_time"}}      # the sample flow takes 6.6 s: fine at 30 s
    cap = capacity(body, load_pricing(), NOW)
    return {p["rule"] for p in consistency.check(body, rec, cap, variant or cheaper(body), CONTEXT, load_pricing(), NOW)}


def test_a_consistent_design_has_no_problems():
    assert check(good(), a_record()) == set()


@pytest.mark.parametrize("changes, rule", [
    ({"targets": {"latency_p95_s": 1.0, "availability_pct": 99.5, "quality": "q"}}, "latency-target"),
    ({"open_risks": [{"text": "x", "failure_mode": "no-such-mode"}]}, "risk-unmapped"),
    ({"runbook": [{"failure_mode": "nope", "alert": "a", "meaning": "m", "first_action": "f"}]}, "runbook-unmapped"),
    ({"scaling": "At 10x volume we need 4 replicas."}, "scaling-numbers"),
])
def test_each_record_mismatch_is_reported(changes, rule):
    assert rule in check(good(), a_record(**changes))


def test_blueprint_side_checks():
    bp = good()
    bp["eval_plan"] = [{"metric": "tone", "method": "manual review", "threshold": "good"}]
    bp["observability"] = ["dashboards"]
    bp["threats"] = [t for t in bp["threats"] if t["component"] != "orders-tool"]
    assert {"eval-threshold", "observability-signal", "threat-missing"} <= check(bp, a_record())


def test_the_variant_must_lint_clean_and_cost_less():
    bp = good()
    assert "variant-cost" in check(bp, a_record(), variant=copy.deepcopy(bp))
    broken = cheaper(bp)
    broken["components"] = broken["components"][:1]
    assert "variant-lint" in check(bp, a_record(), variant=broken)
    no_variant = {p["rule"] for p in consistency.check(bp, a_record(), capacity(bp, load_pricing(), NOW), None, CONTEXT, load_pricing(), NOW)}
    assert not {"variant-missing", "variant-lint", "variant-cost"} & no_variant      # a variant is only checked when asked for


def test_code_measured_alternatives_come_first_and_keep_their_numbers():
    gw = scripted_gateway({"free": [reply(a_record(alternatives=[{"topology": "workflow", "summary": "s",
                                                                  "rejected_because": "model says cheaper"}]))]})
    code_alt = [{"topology": "workflow", "summary": "Fixed steps", "rejected_because": "2.3x the monthly cost"}]
    rec = record.write(gw, good(), capacity(good(), load_pricing(), NOW), code_alt, [])
    assert rec["alternatives"][0]["rejected_because"] == "2.3x the monthly cost" and len(rec["alternatives"]) <= 2


def test_a_long_but_sensible_record_is_valid():
    """Models write full paragraphs; limits that reject them lose the whole record and force a slow rebuild."""
    long = "Keep the workflow topology and ship it as a stateless service. " * 22          # about 1,400 characters
    rec = a_record(decision=long, scaling=long, rollback=long[:700], context=long,
                   targets={"latency_p95_s": 8.0, "availability_pct": 99.5, "quality": "q" * 280},
                   trade_offs=["t" * 500], rollout=["r" * 500, "s"],
                   runbook=[{"failure_mode": "wrong-route", "alert": "a" * 280, "meaning": "m" * 500, "first_action": "f" * 500}])
    assert len(rec["decision"]) > 1000


def test_a_flow_slower_than_the_profiles_latency_target_is_a_problem_naming_the_slowest_step():
    slow = good()                                        # interactive profile: 5 s target, this flow takes about 6.6 s
    problems = [p for p in consistency.check(slow, a_record(), capacity(slow, load_pricing(), NOW), cheaper(slow), CONTEXT,
                                             load_pricing(), NOW) if p["rule"] == "latency-over-target"]
    assert len(problems) == 1 and "answer" in problems[0]["message"] and "faster" in problems[0]["message"]
    relaxed = good()
    relaxed["profile"] = {**relaxed["profile"], "latency": "near_real_time"}
    assert "latency-over-target" not in {p["rule"] for p in consistency.check(
        relaxed, a_record(), capacity(relaxed, load_pricing(), NOW), cheaper(relaxed), CONTEXT, load_pricing(), NOW)}
