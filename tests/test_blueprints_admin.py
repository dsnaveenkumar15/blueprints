import json

import pytest
from fastapi.testclient import TestClient

from blueprints import store
from blueprints.linter import lint
from core import review
from core.db import connect
from tests.blueprint_fixtures import good
from tests.test_blueprints_site import EVIL
from tests.webutil import make_settings
from web.app import create_app

AUTH = ("owner", "correct-horse-battery-staple")
NOW = 1_790_877_600.0


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path, admin_user="owner", admin_password="correct-horse-battery-staple")


@pytest.fixture
def c(settings):
    return TestClient(create_app(settings), base_url="http://blueprints.localhost")


@pytest.fixture
def db(settings):
    conn = connect(settings.db_path)
    store.ensure_schema(conn)
    yield conn
    conn.close()


def draft(db, **over):
    body = {**good(), **over}
    store.save(db, body, lint(body), "generated", "tool graded C", NOW)
    store.request_approval(db, store.get(db, body["id"], 1), NOW, open_issues=["kb: stale policy"])
    return body["id"]


def test_the_review_queue_links_to_the_preview(c, db):
    draft(db)
    html = c.get("/admin", auth=AUTH).text
    assert 'href="/admin/blueprints/refund-support/1"' in html


def test_preview_shows_lint_why_trace_cost_diagram_and_open_issues(c, db):
    draft(db)
    html = c.get("/admin/blueprints/refund-support/1", auth=AUTH).text
    for text in ("Refund support agent", "multiple-task-kinds", "flowchart LR", "per request", "kb: stale policy",
                 "tool graded C", 'action="/admin/reviews/'):
        assert text in html, text


def test_preview_runs_todays_linter_not_the_stored_verdict(c, db, settings):
    draft(db)
    html = c.get("/admin/blueprints/refund-support/1", auth=AUTH).text     # no Trust data published: the tool is ungraded
    assert "tool-not-eligible" in html


def test_a_draft_the_owner_approved_but_the_linter_holds_is_listed(c, db):
    draft(db)
    review.decide(db, review.entry_for(db, store.APPROVAL_KIND, "refund-support@1")["id"], "approved", now=NOW + 1)
    store.sync_decisions(db, NOW + 2, {"trust_grades": {}, "frameworks": set(), "models": {"claude-haiku-4-5", "claude-sonnet-5-5"}})
    html = c.get("/admin", auth=AUTH).text
    assert "Held by the linter" in html and "refund-support" in html and "tool-not-eligible" in html


def test_a_rejected_draft_with_a_raw_body_still_previews(c, db):
    body = good()
    body["flow"][0]["input_tokens"] = "600"
    body["surprise"] = 1                                          # fails the schema, so it was saved rejected
    store.save(db, body, lint(body), "generated", "new (blocked by the linter)", NOW, status="rejected")
    r = c.get("/admin/blueprints/refund-support/1", auth=AUTH)
    assert r.status_code == 200 and "schema" in r.text and "rejected" in r.text


def test_stored_text_is_escaped_in_the_preview(c, db):
    draft(db, title=EVIL, summary=EVIL)
    html = c.get("/admin/blueprints/refund-support/1", auth=AUTH).text
    assert EVIL not in html and "&lt;script&gt;" in html


def test_unknown_drafts_are_404_and_the_preview_needs_credentials(c, db):
    assert c.get("/admin/blueprints/nope/1", auth=AUTH).status_code == 404
    assert c.get("/admin/blueprints/nope/x", auth=AUTH).status_code == 422
    draft(db)
    assert c.get("/admin/blueprints/refund-support/1").status_code == 401


def test_approving_from_the_preview_form_uses_the_normal_review_route(c, db):
    draft(db)
    entry = review.entry_for(db, store.APPROVAL_KIND, "refund-support@1")
    r = c.post(f"/admin/reviews/{entry['id']}", data={"decision": "approved", "note": ""}, auth=AUTH,
               headers={"origin": "http://blueprints.localhost"}, follow_redirects=False)
    assert r.status_code == 303
    assert review.entry_for(db, store.APPROVAL_KIND, "refund-support@1")["state"] == "approved"


def test_a_huge_version_in_the_admin_preview_is_a_clean_404_or_422(c, db):
    r = c.get("/admin/blueprints/refund-support/" + "9" * 40, auth=AUTH)
    assert r.status_code in (404, 422)


def test_the_preview_keeps_the_why_trace_when_only_the_cost_fails(c, db):
    body = good()
    body["flow"][0]["input_tokens"] = "600"          # the cost arithmetic cannot take this; the decision engine can
    store.save(db, body, {"ok": False, "errors": 1, "warnings": 0, "violations": []}, "generated", "new", NOW, status="rejected")
    html = c.get("/admin/blueprints/refund-support/1", auth=AUTH).text
    assert "Why this topology" in html and "multiple-task-kinds" in html and "flowchart LR" in html
