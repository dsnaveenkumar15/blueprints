import copy
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from blueprints.cost import estimate, load_pricing
from tests.blueprint_fixtures import good
from tests.test_blueprints_site import EVIL, build_release, client, settings  # noqa: F401  (fixture re-export)
from web.blueprints_site import cost_data

ROOT = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
NOW = 1_790_877_600.0


def run_node(expression: str):
    script = f"const m = require('./web/static/blueprints-cost.js'); console.log(JSON.stringify({expression}));"
    return json.loads(subprocess.run([NODE, "-e", script], cwd=ROOT, capture_output=True, text=True, check=True).stdout)


def test_priced_steps_carry_the_inputs_the_sliders_need():
    cost = estimate(good(), load_pricing(), NOW)
    assert set(cost["steps"][0]["inputs"]) == {"input_tokens", "output_tokens", "calls", "input_per_mtok", "output_per_mtok",
                                               "ttft_s", "tokens_per_s"}
    bp = good()
    bp["components"][0]["model"] = "gpt-imaginary"
    assert estimate(bp, load_pricing(), NOW)["steps"][0]["inputs"] is None


def test_cost_data_is_incomplete_when_any_step_is_unpriced():
    bp = good()
    bp["components"][0]["model"] = "gpt-imaginary"
    data = cost_data(estimate(bp, load_pricing(), NOW))
    assert data["complete"] is False and len(data["steps"]) == 1


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_slider_math_matches_the_python_estimate():
    pricing, body = load_pricing(), good()
    data = cost_data(estimate(body, pricing, NOW))
    js = run_node(f"m.estimate({json.dumps(data)}, 123456, 2.5)")
    scaled = copy.deepcopy(body)
    for step in scaled["flow"]:
        step["input_tokens"] *= 2.5
        step["output_tokens"] *= 2.5
    py = estimate(scaled, pricing, NOW, volume_per_day=123456)
    assert js["monthly"] == pytest.approx(py["monthly_usd"], abs=0.01)
    assert js["perRequest"] == pytest.approx(py["per_request_usd"], abs=1e-6)
    assert js["latency"] == pytest.approx(py["latency_s"], abs=0.01)
    assert js["over"] is py["over_budget"]


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_odd_slider_values_are_clamped_never_nan():
    data = cost_data(estimate(good(), load_pricing(), NOW))
    out = run_node(f"[m.estimate({json.dumps(data)}, 'abc', -5), m.estimate({json.dumps(data)}, 1e30, 1e30),"
                   f" m.estimate({json.dumps(data)}, 0, 0)]")
    for r in out:
        assert all(isinstance(r[k], (int, float)) and r[k] == r[k] for k in ("perRequest", "monthly", "latency", "volume"))
    assert out[0]["volume"] == data["volume"] and out[1]["volume"] == 100_000_000


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_incomplete_costs_never_show_a_monthly_number():
    data = cost_data(estimate(good(), load_pricing(), NOW))
    data["complete"] = False
    assert run_node(f"m.estimate({json.dumps(data)}, 1000, 1)")["monthly"] is None


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_volume_slider_is_logarithmic_and_round_trips_to_a_nearby_value():
    out = run_node("[m.sliderToVolume(0), m.sliderToVolume(300), m.sliderToVolume(800), m.volumeToSlider(1000)]")
    assert out == [1, 1000, 100_000_000, 300]


def test_detail_page_embeds_slider_data_that_cannot_close_its_script_block(settings):
    evil = {**good(), "id": "evil-one", "title": EVIL}
    evil["components"][3].pop("tool_ref")
    evil["components"][0]["id"] = "router-model"
    build_release(settings, extra=[evil])
    c = client(settings)
    html = c.get("/b/evil-one").text
    assert '<script src="/static/blueprints-cost.js"></script>' in html
    assert 'id="cost-data" type="application/json"' in html and 'id="volume"' in html and 'id="scale"' in html
    block = html.split('id="cost-data" type="application/json">')[1].split("</script>")[0]
    assert json.loads(block)["complete"] is True and "<" not in block
    assert c.get("/static/blueprints-cost.js").status_code == 200


@pytest.mark.skipif(NODE is None, reason="node is not installed")
def test_an_incomplete_estimate_never_prints_a_partial_per_request_price():
    data = cost_data(estimate(good(), load_pricing(), NOW))
    r = f"m.estimate({json.dumps(data)}, 1000, 1)"
    complete, partial = run_node(f"[m.summary({r}, true), m.summary({r}, false)]")
    assert "per request" in complete and "/ month" in complete
    assert "per request" not in partial and "incomplete" in partial
