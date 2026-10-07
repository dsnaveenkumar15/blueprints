# tests/test_blueprints_architect_capacity.py
from blueprints.architect.capacity import PEAK_FACTOR, capacity
from blueprints.architect.ground import ground
from blueprints.cost import load_pricing
from blueprints.decision import decide
from tests.blueprint_fixtures import CONTEXT, good

NOW = 1_790_877_600.0


def test_numbers_come_from_the_flow_and_the_price_table():
    bp = good()
    cap = capacity(bp, load_pricing(), NOW)
    assert cap["complete"] and [s["scale"] for s in cap["scales"]] == [1, 10, 100]
    one, hundred = cap["scales"][0], cap["scales"][2]
    assert one["peak_rps"] == round(5000 / 86400 * PEAK_FACTOR, 3)
    assert abs(hundred["monthly_usd"] - one["monthly_usd"] * 100) < 1
    assert cap["latency_target_s"] == 5.0 and cap["within_target"] is (cap["latency_s"] <= 5.0)


def test_the_first_limit_names_latency_when_the_flow_is_too_slow():
    cap = capacity(good(), load_pricing(), NOW)            # the fixture's flow takes about 6.6 s against 5 s
    assert cap["first_limit"]["kind"] == "latency" and "answer" in cap["first_limit"]["text"]


def test_the_first_limit_names_the_budget_crossing_when_latency_is_fine():
    bp = good()
    bp["profile"] = {**bp["profile"], "latency": "batch", "budget_usd_month": 2000}
    cap = capacity(bp, load_pricing(), NOW)
    assert cap["first_limit"]["kind"] == "budget" and "10x" in cap["first_limit"]["text"]


def test_an_unpriced_model_makes_the_capacity_incomplete_not_guessed():
    bp = good()
    bp["components"][0]["model"] = "unknown-model"
    cap = capacity(bp, load_pricing(), NOW)
    assert not cap["complete"] and cap["first_limit"]["kind"] == "unpriced"


def test_grounding_brings_the_nearest_published_designs_and_the_allowed_parts():
    bp = good()
    index = [{"id": "refund-support", "title": "Refund support agent", "topology": "router", "risk": "high",
              "profile": bp["profile"]}]
    out = ground(bp["profile"], decide(bp["profile"]), index, lambda i: {"body": bp}, [{"id": "routing"}, {"id": "react"}],
                 CONTEXT, load_pricing())
    assert out["references"][0]["blueprint"]["id"] == "refund-support"
    assert [p["id"] for p in out["patterns"]] == ["routing"]
    assert "claude-haiku-4-5" in out["limits"]["allowed_models"] and "acme/orders-mcp" in out["limits"]["allowed_tools"]
