import pytest

from blueprints.decision import RULES, decide
from blueprints.models import TOPOLOGIES

BASE = {"latency": "batch", "volume_per_day": 100, "risk": "low", "data_sensitivity": "public", "autonomy": "suggest",
        "budget_usd_month": 100, "capabilities": ["read"], "open_ended": False, "task_kinds": 1,
        "parallelizable": False, "knowledge": False, "quality_critical": False}


def profile(**over):
    return {**BASE, **over}


def kinds(decision):
    return sorted(m["kind"] for m in decision["mandatory"])


GOLDEN = [
    ("support agent", profile(latency="interactive", volume_per_day=5000, risk="high", data_sensitivity="pii",
                              autonomy="act_with_approval", capabilities=["read", "write"], task_kinds=3, knowledge=True),
     "router", ["audit_log", "human_gate", "input_filter", "retrieval"], ["routing", "rag", "hitl-approval"]),
    ("batch summarizer", profile(), "workflow", [], ["prompt-chaining"]),
    ("research agent", profile(latency="near_real_time", risk="medium", data_sensitivity="internal",
                               autonomy="act_with_approval", capabilities=["read", "network"], open_ended=True,
                               parallelizable=True),
     "orchestrator-workers", ["human_gate", "rate_limit", "sandbox"], ["orchestrator-workers", "parallelization", "hitl-approval"]),
    ("autonomous coder", profile(risk="medium", data_sensitivity="internal", autonomy="act_autonomously",
                                 capabilities=["read", "write", "exec"], open_ended=True),
     "autonomous", ["human_gate", "rate_limit", "sandbox"], ["react", "hitl-approval"]),
    ("interactive open-ended", profile(latency="interactive", risk="high", open_ended=True), "workflow", [], ["prompt-chaining"]),
    ("quality critical", profile(quality_critical=True), "evaluator-optimizer", [], ["evaluator-optimizer"]),
    ("regulated data", profile(data_sensitivity="regulated"), "workflow", ["audit_log", "input_filter", "output_filter"],
     ["prompt-chaining"]),
    ("fan-out batch", profile(parallelizable=True), "workflow", [], ["prompt-chaining", "parallelization"]),
    ("lead model", profile(open_ended=True, risk="medium", autonomy="act_with_approval"), "supervisor", ["rate_limit"],
     ["handoff"]),
]


@pytest.mark.parametrize("name, prof, topology, mandatory, patterns", GOLDEN, ids=[g[0] for g in GOLDEN])
def test_golden_profiles(name, prof, topology, mandatory, patterns):
    d = decide(prof)
    assert d["topology"] == topology and kinds(d) == mandatory and d["patterns"] == patterns


def test_interactive_latency_removes_multi_agent_topologies_and_says_why():
    d = decide(profile(latency="interactive", open_ended=True, risk="medium", autonomy="act_autonomously"))
    assert d["allowed_topologies"] == ["workflow", "router"] and d["topology"] == "workflow"
    rules = [t["rule"] for t in d["trace"]]
    assert "interactive-latency" in rules and "prefer-workflow" not in rules
    assert all({"rule", "effect", "because"} <= set(t) for t in d["trace"])


def test_high_risk_never_allows_autonomous():
    d = decide(profile(risk="high", open_ended=True, autonomy="act_autonomously"))
    assert "autonomous" not in d["allowed_topologies"] and d["topology"] != "autonomous"


def test_a_workflow_is_preferred_unless_the_task_is_open_ended():
    assert decide(profile(autonomy="act_autonomously"))["topology"] == "workflow"
    assert "autonomous" not in decide(profile())["allowed_topologies"] and "supervisor" not in decide(profile())["allowed_topologies"]


def test_autonomous_write_gets_an_approval_gate():
    d = decide(profile(risk="medium", autonomy="act_autonomously", capabilities=["write"], open_ended=True))
    assert d["topology"] == "autonomous" and "autonomous-write-gate" in [m["rule"] for m in d["mandatory"]]


def test_volume_alone_requires_a_rate_limit():
    assert "rate_limit" in kinds(decide(profile(volume_per_day=10_000)))
    assert "rate_limit" not in kinds(decide(profile(volume_per_day=9_999)))


def test_same_input_gives_the_same_output_and_models_are_accepted():
    from blueprints.models import Profile
    assert decide(profile()) == decide(Profile(**profile()))
    assert decide(profile(task_kinds=2))["topology"] == "router"


def test_every_rule_in_a_trace_or_mandatory_list_is_documented():
    seen = set()
    for _, prof, *_ in GOLDEN:
        d = decide(prof)
        seen |= {t["rule"] for t in d["trace"]} | {m["rule"] for m in d["mandatory"]}
    assert seen <= set(RULES) and set(d["allowed_topologies"]) <= set(TOPOLOGIES)
