# tests/test_blueprints_architect_alternatives.py
from blueprints.architect.alternatives import from_decision
from blueprints.decision import RULES, decide
from tests.blueprint_fixtures import good


def test_ruled_out_topologies_cite_the_rule_that_removed_them():
    d = decide(good()["profile"])                       # interactive, several request kinds: a router
    alts = {a["topology"]: a for a in from_decision(d)}
    assert d["topology"] == "router" and "router" not in alts
    assert RULES["interactive-latency"] in alts["orchestrator-workers"]["rejected_because"]


def test_an_allowed_topology_that_lost_cites_the_rule_that_chose_the_winner():
    d = decide(good()["profile"])
    workflow = next(a for a in from_decision(d) if a["topology"] == "workflow")
    assert RULES["multiple-task-kinds"] in workflow["rejected_because"] and "router" in workflow["rejected_because"]


def test_at_most_three_alternatives_the_closest_first():
    alts = from_decision(decide(good()["profile"]))
    assert 1 <= len(alts) <= 3 and alts[0]["topology"] == "workflow"
    assert all(a["summary"] and a["rejected_because"] for a in alts)


def test_an_unconstrained_profile_still_explains_every_alternative():
    profile = {**good()["profile"], "latency": "batch", "risk": "low", "open_ended": True, "parallelizable": True,
               "autonomy": "act_autonomously"}
    d = decide(profile)
    assert all(a["rejected_because"] for a in from_decision(d)) and d["topology"] not in {a["topology"] for a in from_decision(d)}
