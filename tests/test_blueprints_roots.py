import json

from fastapi.testclient import TestClient

from core.publish import publish
from tests.blueprint_fixtures import good
from tests.webutil import make_settings
from web.app import create_app


def put(root, planet, dataset, files):
    return publish(root, planet, dataset, {n: json.dumps(v).encode() for n, v in files.items()}, {})


def test_blueprints_lint_uses_the_configured_trust_and_radar_roots(tmp_path):
    put(tmp_path / "t", "trust", "directory", {"index.json": {"count": 1, "servers": [{"repo": "acme/orders-mcp", "grade": "B"}]}})
    put(tmp_path / "r", "radar", "digests", {"packages.json": {"packages": {"pypi:langgraph": {}}}})
    settings = make_settings(tmp_path / "own", trust_data_root=str(tmp_path / "t"), radar_data_root=str(tmp_path / "r"))
    c = TestClient(create_app(settings), base_url="http://blueprints.localhost")
    bp = good()
    bp["components"][1]["framework_ref"] = "pypi:langgraph"
    rules = {v["rule"] for v in c.post("/api/lint-blueprint", json=bp).json()["lint"]["violations"]}
    assert "tool-not-eligible" not in rules and "unknown-framework" not in rules


def test_blueprints_still_work_when_the_other_roots_do_not_exist(tmp_path):
    settings = make_settings(tmp_path / "own", trust_data_root=str(tmp_path / "missing-t"), radar_data_root=str(tmp_path / "missing-r"))
    c = TestClient(create_app(settings), base_url="http://blueprints.localhost")
    r = c.post("/api/lint-blueprint", json=good())
    assert r.status_code == 200 and any(v["rule"] == "tool-not-eligible" for v in r.json()["lint"]["violations"])
    assert c.get("/").status_code == 200 and c.get("/finder").status_code == 200 and c.get("/custom").status_code == 200
