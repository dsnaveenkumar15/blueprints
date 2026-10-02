import json

import pytest
from fastapi.testclient import TestClient

from core.publish import publish
from tests.blueprint_fixtures import good
from tests.test_blueprints_site import settings  # noqa: F401  (fixture re-export)
from web.app import create_app
from web.ratelimit import RateLimiter

NOW = 1_790_877_600.0


@pytest.fixture
def app(settings):
    return create_app(settings)


def blueprints(app):
    return TestClient(app, base_url="http://blueprints.localhost")


def post(c, payload, **kwargs):
    return c.post("/api/lint-blueprint", content=json.dumps(payload), headers={"content-type": "application/json"}, **kwargs)


def publish_trust(root, servers):
    publish(root, "trust", "directory", {"index.json": json.dumps({"count": len(servers), "servers": servers}).encode()}, {})


def publish_radar(root, packages):
    publish(root, "radar", "digests", {"packages.json": json.dumps({"generated_at": NOW, "packages": packages}).encode(),
                                       "index.json": b'{"digests": []}'}, {})


def test_a_good_blueprint_is_ok_and_comes_with_its_decision_and_cost(app):
    r = post(blueprints(app), {"blueprint": {**good(), "components": [
        {k: v for k, v in c.items() if k != "tool_ref"} for c in good()["components"]]}})
    body = r.json()
    assert r.status_code == 200 and body["lint"]["ok"] is True
    assert body["decision"]["topology"] == "router" and body["cost"]["complete"] is True


def test_a_bare_blueprint_object_is_accepted_too(app):
    bp = good()
    bp["components"][3].pop("tool_ref")
    assert post(blueprints(app), bp).json()["lint"]["ok"] is True


def test_published_trust_and_radar_data_are_the_lint_context(app, settings):
    publish_trust(settings.data_root, [{"repo": "acme/orders-mcp", "grade": "C"}])
    publish_radar(settings.data_root, {})
    bp = good()
    bp["components"][1]["framework_ref"] = "pypi:langgraph"
    rules = {v["rule"] for v in post(blueprints(app), bp).json()["lint"]["violations"]}
    assert {"tool-not-eligible", "unknown-framework"} <= rules


def test_a_schema_failure_has_no_decision_or_cost(app):
    bad = good()
    bad["surprise"] = 1
    body = post(blueprints(app), bad).json()
    assert body["lint"]["ok"] is False and body["decision"] is None and body["cost"] is None


def test_a_quoted_number_is_judged_and_never_crashes_the_cost(app):
    bp = good()
    bp["components"][3].pop("tool_ref")
    bp["flow"][0]["input_tokens"] = "600"
    r = post(blueprints(app), bp)
    assert r.status_code == 200 and r.json()["cost"]["complete"] is True


@pytest.mark.parametrize("payload", [[], None, "text", 42, {}, {"blueprint": None}, {"blueprint": []}, [1, 2, 3]])
def test_odd_json_values_get_a_verdict_not_a_crash(app, payload):
    r = post(blueprints(app), payload)
    assert r.status_code == 200 and r.json()["lint"]["ok"] is False


def test_invalid_json_and_absurd_nesting_are_400(app):
    c = blueprints(app)
    assert c.post("/api/lint-blueprint", content="{nope", headers={"content-type": "application/json"}).status_code == 400
    assert c.post("/api/lint-blueprint", content="[" * 100_000, headers={"content-type": "application/json"}).status_code == 400


def test_oversized_bodies_are_413(app):
    c = blueprints(app)
    assert c.post("/api/lint-blueprint", content='{"a": "' + "x" * 250_000 + '"}',
                  headers={"content-type": "application/json"}).status_code == 413


def test_it_is_only_on_the_blueprints_host_and_rate_limited(app):
    radar = TestClient(app, base_url="http://radar.localhost")
    assert post(radar, good()).status_code == 404
    app.state.lint_limiter = RateLimiter(per_minute=1, burst=1)
    c = blueprints(app)
    assert post(c, good()).status_code == 200
    assert post(c, good()).status_code == 429


def test_the_lint_context_is_built_off_the_event_loop(app, monkeypatch):
    import threading

    from web import blueprints_api
    seen = []
    original = blueprints_api.lint_context

    def spy(request):
        seen.append(threading.current_thread().name)
        return original(request)
    monkeypatch.setattr(blueprints_api, "lint_context", spy)
    assert post(blueprints(app), good()).status_code == 200
    assert seen and seen[0].startswith("AnyIO worker")
