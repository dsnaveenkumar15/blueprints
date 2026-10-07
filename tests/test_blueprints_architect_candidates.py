# tests/test_blueprints_architect_candidates.py
import pytest

pytest.importorskip("langchain_core")

from blueprints.architect import candidates  # noqa: E402
from blueprints.cost import load_pricing  # noqa: E402
from blueprints.decision import decide  # noqa: E402
from tests.blueprint_fixtures import CONTEXT, good, reply, scripted_gateway  # noqa: E402

NOW = 1_790_877_600.0


def test_slugs_are_valid_blueprint_ids():
    assert candidates.slug("Refund helper for the EU shop!") == "refund-helper-for-the-eu-shop"
    assert candidates.slug("42 bots") == "d-42-bots" and candidates.slug("!!!") == "design"


def test_the_primary_topology_comes_first_and_the_alternative_is_allowed():
    d = decide(good()["profile"])                    # interactive: only workflow and router are allowed
    tops = candidates.topologies(d)
    assert tops[0] == d["topology"] and len(tops) == 2 and set(tops) <= set(d["allowed_topologies"])


def test_a_draft_cannot_change_its_id_profile_or_topology():
    sneaky = {**good(), "id": "other-id", "topology": "autonomous",
              "profile": {**good()["profile"], "risk": "low", "autonomy": "act_autonomously"}}
    gw = scripted_gateway({"free": [reply({"blueprint": sneaky})]})
    b = candidates.brief("Refund support agent", "customer support", good()["profile"])
    body = candidates.draft(gw, "router", b, decide(b["profile"]), {"limits": {}, "references": [], "patterns": []})
    assert body["id"] == "refund-support-agent" and body["topology"] == "router" and body["profile"] == good()["profile"]


def test_scoring_prefers_the_clean_cheaper_design_and_explains_the_loser_with_numbers():
    d = decide(good()["profile"])
    cheap = good()
    pricey = good()
    pricey["topology"] = "workflow"
    pricey["flow"][1]["input_tokens"] *= 4
    a = candidates.score(cheap, d, CONTEXT, load_pricing(), NOW)
    b = candidates.score(pricey, d, CONTEXT, load_pricing(), NOW)
    assert candidates.choose([b, a]) == 1
    reasons = candidates.penalties(a, b)
    assert any("x the monthly cost" in r for r in reasons)


def test_topology_differs_is_not_counted_as_a_warning():
    alt = good()
    alt["topology"] = "workflow"
    s = candidates.score(alt, decide(alt["profile"]), CONTEXT, load_pricing(), NOW)
    assert s["warnings"] == 0 and not s["primary"]
