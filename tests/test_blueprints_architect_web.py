# tests/test_blueprints_architect_web.py
import time

import pytest
from fastapi.testclient import TestClient

pytest.importorskip("langgraph")

from blueprints.architect import runs  # noqa: E402
from blueprints.architect.clarify import DEFAULTS, questions  # noqa: E402
from core.db import connect  # noqa: E402
from tests.test_blueprints_architect_render import result as finished  # noqa: E402
from tests.webutil import make_settings  # noqa: E402
from web.app import create_app  # noqa: E402

H = {"origin": "http://blueprints.localhost"}
EVIL = "<script>alert(1)</script>"


@pytest.fixture
def client(tmp_path, monkeypatch):
    monkeypatch.setenv("ARCHITECT_ENABLED", "1")
    monkeypatch.setenv("OPENROUTER_API_KEY", "k")
    monkeypatch.setenv("ARCHITECT_FAST_MODELS", "paid")
    app = create_app(make_settings(tmp_path))
    started = []
    app.state.architect_runner.start = lambda rid: started.append(rid)
    c = TestClient(app, base_url="http://blueprints.localhost")
    c.started = started
    c.conn = connect(app.state.settings.db_path)
    runs.STORE.ensure_schema(c.conn)
    return c


def waiting(c):
    rid = runs.STORE.create_run(c.conn, {"phase": "clarify", "description": "d", "consent": True}, "new", "x", time.time())
    qs = questions(dict(DEFAULTS), [])
    runs.STORE.finish(c.conn, rid, "waiting", {"title": "T", "use_case": "u", "profile": dict(DEFAULTS), "known": [],
                                              "questions": qs}, None, time.time())
    return rid, qs


def test_the_page_has_the_form_consent_and_limits(client):
    html = client.get("/architect").text
    assert 'id="arch-form"' in html and 'id="arch-consent"' in html and "deleted after 7 days" in html
    assert 'href="/architect"' in client.get("/").text


def test_starting_needs_consent_and_one_design_at_a_time(client):
    assert client.post("/api/architect/runs", json={"description": "x", "consent": False}, headers=H).status_code == 400
    r = client.post("/api/architect/runs", json={"description": "An agent that answers refunds", "consent": True}, headers=H)
    assert r.status_code == 200 and len(r.json()["id"]) == 32 and client.started == [r.json()["id"]]
    again = client.post("/api/architect/runs", json={"description": "another", "consent": True}, headers=H)
    assert again.status_code == 429 and "one at a time" in again.json()["detail"]


def test_cross_site_and_oversized_posts_are_refused(client):
    assert client.post("/api/architect/runs", json={"description": "x", "consent": True},
                       headers={"origin": "https://evil.example"}).status_code == 403
    assert client.post("/api/architect/runs", json={"description": "x" * 2001, "consent": True}, headers=H).status_code == 422


def test_answers_resume_the_run_once_and_only_from_the_offered_options(client):
    rid, qs = waiting(client)
    page = client.get(f"/architect/r/{rid}").text
    assert 'id="arch-answers"' in page and qs[0]["text"] in page
    bad = client.post(f"/api/architect/runs/{rid}/answers", json={"answers": {"risk": 99}}, headers=H)
    assert bad.status_code == 400
    ok = client.post(f"/api/architect/runs/{rid}/answers", json={"answers": {qs[0]["field"]: 0}}, headers=H)
    assert ok.status_code == 200 and client.started == [rid]
    run = runs.STORE.get_run(client.conn, rid)
    assert run["status"] == "queued" and run["input"]["phase"] == "design" and run["input"]["assumptions"]
    twice = client.post(f"/api/architect/runs/{rid}/answers", json={"answers": {}}, headers=H)
    assert twice.status_code == 409 and client.started == [rid]


def test_a_finished_page_is_private_escaped_and_downloadable_only_when_passed(client):
    rid = runs.STORE.create_run(client.conn, {"phase": "design", "consent": True}, "new", "x", time.time())
    res = finished()
    res["blueprint"]["summary"] = EVIL
    runs.STORE.finish(client.conn, rid, "passed", res, None, time.time())
    page = client.get(f"/architect/r/{rid}")
    assert page.status_code == 200 and EVIL not in page.text and "&lt;script&gt;" in page.text
    assert page.headers["x-robots-tag"] == "noindex" and page.headers["referrer-policy"] == "no-referrer"
    assert 'id="arch-refine"' in page.text and "Passed review" in page.text
    md = client.get(f"/architect/r/{rid}.md")
    assert md.status_code == 200 and "## Decision record" in md.text and md.headers["x-robots-tag"] == "noindex"
    assert client.get(f"/architect/r/{rid}.json").json()["verdict"] == "passed"
    runs.STORE.finish(client.conn, rid, "not_ready", {**res, "verdict": "not_ready"}, None, time.time())
    assert client.get(f"/architect/r/{rid}.md").status_code == 404


def test_unknown_and_malformed_ids_are_404(client):
    assert client.get("/architect/r/" + "0" * 32).status_code == 404
    assert client.get("/architect/r/not-an-id").status_code == 404
    assert client.get("/api/architect/runs/" + "0" * 32 + "/events").status_code == 404


def test_refine_starts_a_new_run_from_the_previous_design(client):
    rid = runs.STORE.create_run(client.conn, {"phase": "design", "consent": True}, "new", "y", time.time())
    runs.STORE.finish(client.conn, rid, "passed", finished(), None, time.time())
    r = client.post(f"/api/architect/runs/{rid}/refine", json={"instruction": "budget $300 a month"}, headers=H)
    assert r.status_code == 200
    child = runs.STORE.get_run(client.conn, r.json()["id"])
    assert child["mode"] == "refine" and child["input"]["previous"]["id"] and child["input"]["parent"] == rid


def test_a_design_older_than_seven_days_is_gone_when_its_page_is_opened(client):
    rid = runs.STORE.create_run(client.conn, {"phase": "design", "consent": True}, "new", "z", time.time() - 8 * 86400)
    runs.STORE.finish(client.conn, rid, "passed", finished(), None, time.time() - 8 * 86400)
    assert client.get(f"/architect/r/{rid}").status_code == 404
    assert runs.STORE.get_run(client.conn, rid) is None
