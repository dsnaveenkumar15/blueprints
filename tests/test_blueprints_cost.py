import json

import pytest

from blueprints.cost import estimate, is_stale, load_pricing
from tests.blueprint_fixtures import good

NOW = 1_790_877_600.0                       # 2026-10-01


def test_the_shipped_table_is_complete_dated_and_sourced():
    p = load_pricing()
    assert p["source"].startswith("https://") and p["as_of"] == "2026-10-01" and p["max_age_days"] == 45
    assert {"claude-fable-5-1", "claude-opus-5-5", "claude-sonnet-5-5", "claude-haiku-4-5"} <= set(p["models"])
    for model, m in p["models"].items():
        assert m["input_per_mtok"] >= 0 and m["output_per_mtok"] >= 0 and m["ttft_s"] > 0 and m["tokens_per_s"] > 0, model
    assert p["models"]["claude-haiku-4-5"]["input_per_mtok"] == 1.0 and p["models"]["claude-opus-5-5"]["output_per_mtok"] == 20.0
    local = p["models"]["ollama-qwen2.5-1.5b"]
    assert local["input_per_mtok"] == 0 and local["output_per_mtok"] == 0 and local["self_hosted"] is True
    assert p["latency_status"] == "assumed" and not is_stale(p, NOW)


def test_estimate_arithmetic_from_tokens_prices_and_volume():
    e = estimate(good(), load_pricing(), NOW)
    assert [s["id"] for s in e["steps"]] == ["classify", "answer"]
    assert e["steps"][0]["usd"] == pytest.approx(0.00085) and e["steps"][1]["usd"] == pytest.approx(0.009)
    assert e["per_request_usd"] == pytest.approx(0.00985) and e["monthly_usd"] == pytest.approx(1477.5)
    assert e["latency_s"] == pytest.approx(6.63, abs=0.01) and e["complete"] is True and e["unpriced"] == []
    assert e["over_budget"] is False and e["volume_per_day"] == 5000 and e["budget_usd_month"] == 2000
    assert e["pricing"] == {"as_of": "2026-10-01", "source": load_pricing()["source"], "stale": False,
                            "latency_status": "assumed"}
    assert any("one after another" in a for a in e["assumptions"])


def test_volume_override_and_budget_flag():
    e = estimate(good(), load_pricing(), NOW, volume_per_day=20_000)
    assert e["monthly_usd"] == pytest.approx(5910.0) and e["over_budget"] is True and e["volume_per_day"] == 20_000
    assert estimate(good(), load_pricing(), NOW, volume_per_day=0)["volume_per_day"] == 1       # sliders cannot go below 1


def test_repeated_calls_scale_cost_and_latency():
    bp = good()
    bp["flow"][1]["calls"] = 3
    e = estimate(bp, load_pricing(), NOW)
    assert e["steps"][1]["usd"] == pytest.approx(0.027) and e["steps"][1]["latency_s"] == pytest.approx(17.4)


def test_an_unpriced_model_is_never_guessed():
    bp = good()
    bp["components"][1]["model"] = "gpt-imaginary"
    e = estimate(bp, load_pricing(), NOW)
    assert e["complete"] is False and e["unpriced"] == ["answer"] and e["monthly_usd"] is None
    assert e["over_budget"] is False and e["per_request_usd"] == pytest.approx(0.00085)
    broken = good()
    broken["flow"][0]["component"] = "ghost"
    assert estimate(broken, load_pricing(), NOW)["unpriced"] == ["classify"]


def test_self_hosted_models_cost_nothing():
    bp = good()
    for c in bp["components"][:2]:
        c["model"] = "ollama-qwen2.5-1.5b"
    e = estimate(bp, load_pricing(), NOW)
    assert e["per_request_usd"] == 0 and e["monthly_usd"] == 0 and e["complete"] is True


def test_staleness_is_flagged_after_the_maximum_age():
    p = load_pricing()
    assert is_stale(p, NOW + 46 * 86400) and not is_stale(p, NOW + 44 * 86400)
    assert estimate(good(), p, NOW + 60 * 86400)["pricing"]["stale"] is True


@pytest.mark.parametrize("bad", [
    {}, {"as_of": "2026-10-01", "source": "https://x.test", "max_age_days": 45, "models": {}},
    {"as_of": "not a date", "source": "https://x.test", "max_age_days": 45,
     "models": {"m": {"input_per_mtok": 1, "output_per_mtok": 1, "ttft_s": 1, "tokens_per_s": 1}}},
    {"as_of": "2026-10-01", "source": "https://x.test", "max_age_days": 45,
     "models": {"m": {"input_per_mtok": "free", "output_per_mtok": 1, "ttft_s": 1, "tokens_per_s": 1}}},
    {"as_of": "2026-10-01", "source": "", "max_age_days": 45,
     "models": {"m": {"input_per_mtok": 1, "output_per_mtok": 1, "ttft_s": 1, "tokens_per_s": 1}}}])
def test_malformed_pricing_files_are_rejected(tmp_path, bad):
    path = tmp_path / "pricing.json"
    path.write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError):
        load_pricing(path)


def test_token_counts_are_labelled_as_authored_estimates_approved_by_the_owner():
    from blueprints.cost import estimate, load_pricing
    from tests.blueprint_fixtures import good
    assumptions = estimate(good(), load_pricing(), 1_790_877_600.0)["assumptions"]
    assert any("token counts" in a and "owner" in a for a in assumptions)


def test_the_fixture_estimate_still_adds_up_with_step_inputs():
    from blueprints.cost import estimate, load_pricing
    from tests.blueprint_fixtures import good
    cost = estimate(good(), load_pricing(), 1_790_877_600.0)
    assert round(sum(s["usd"] for s in cost["steps"]), 5) == round(cost["per_request_usd"], 5)
