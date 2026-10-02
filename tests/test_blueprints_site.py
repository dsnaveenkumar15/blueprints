import json

import pytest
from fastapi.testclient import TestClient

from blueprints import store
from blueprints.linter import lint
from blueprints.publishing import publish_library
from core.db import connect
from tests.blueprint_fixtures import good
from tests.webutil import make_settings
from web.app import create_app

NOW = 1_790_877_600.0
EVIL = "<script>alert(1)</script>"
PACKAGES = {"pypi:langgraph": {"latest_stable": "1.2.12", "advisories": [], "last_breaking": None}}


@pytest.fixture
def settings(tmp_path):
    return make_settings(tmp_path)


def build_release(settings, extra=(), grades=None, packages=None, now=NOW, refresh=(), seeds=True):
    """Publish a real blueprints/library release into the settings' data root."""
    conn = connect(settings.db_path)
    store.ensure_schema(conn)
    if seeds:
        store.seed(conn, 1.0)
    for body in extra:
        store.save(conn, body, lint(body), "generated", "new", 2.0, status="approved",
                   provenance={"model": "sonnet", "approval_id": 7, "prompts": ["blueprints.draft@2"]})
    for blueprint_id, reason in refresh:
        store.add_refresh(conn, blueprint_id, reason, 1, now)
    publish_library(conn, settings.data_root, now, grades=grades or {}, packages=packages or PACKAGES)
    conn.close()


def client(settings):
    return TestClient(create_app(settings), base_url="http://blueprints.localhost")


def test_an_empty_site_says_so_instead_of_failing(settings):
    r = client(settings).get("/")
    assert r.status_code == 200 and "No blueprints published yet" in r.text


def test_the_library_lists_every_published_blueprint_with_its_facts(settings):
    build_release(settings)
    html = client(settings).get("/").text
    for blueprint_id in ("support-agent", "docs-rag", "code-review-agent", "dispute-handling"):
        assert f'href="/b/{blueprint_id}"' in html
    assert "router" in html and "orchestrator-workers" in html


def test_filters_narrow_the_list_and_unknown_values_or_keys_are_harmless(settings):
    build_release(settings)
    c = client(settings)
    html = c.get("/?topology=workflow").text
    assert 'href="/b/docs-rag"' in html and 'href="/b/support-agent"' not in html
    assert 'href="/b/docs-rag"' not in c.get("/?risk=high&topology=workflow").text
    nothing = c.get("/?topology=nonsense&surprise=1")
    assert nothing.status_code == 200 and "No blueprint matches these filters" in nothing.text


def test_stored_text_is_escaped(settings):
    evil = {**good(), "id": "evil-one", "title": EVIL, "summary": EVIL}
    evil["components"][3].pop("tool_ref")
    build_release(settings, extra=[evil])
    html = client(settings).get("/").text
    assert EVIL not in html and "&lt;script&gt;" in html


def test_a_row_that_could_not_be_published_is_reported_to_visitors_as_a_count(settings):
    conn = connect(settings.db_path)
    store.ensure_schema(conn)
    body = {**good(), "id": "bad-one"}
    body["components"][3].pop("tool_ref")
    store.save(conn, body, lint(body), "generated", "new", 2.0, status="approved")
    broken = json.loads(json.dumps(body))
    broken["flow"][0]["input_tokens"] = "600"
    conn.execute("UPDATE blueprints SET body=? WHERE id='bad-one'", (json.dumps(broken),))
    publish_library(conn, settings.data_root, NOW, grades={}, packages=PACKAGES)
    conn.close()
    assert "1 blueprint could not be shown" in client(settings).get("/").text


def test_other_hosts_are_untouched(settings):
    build_release(settings)
    radar = TestClient(create_app(settings), base_url="http://radar.localhost")
    assert 'href="/b/docs-rag"' not in radar.get("/").text
    assert radar.get("/patterns").status_code == 404
    assert client(settings).get("/static/site.css").status_code == 200


def with_tool(grade_blueprint=None):
    body = {**good(), "id": "tooled-one"}
    return body


def test_detail_shows_the_why_trace_components_failure_modes_and_eval_plan(settings):
    build_release(settings)
    r = client(settings).get("/b/support-agent")
    assert r.status_code == 200
    for text in ("Why this topology", "multiple-task-kinds", "Components", "Failure modes", "Eval plan",
                 "Threats", "LLM06", "Excessive Agency", "flowchart LR", "seed"):
        assert text in r.text, text


def test_a_tool_below_b_is_shown_as_not_recommended_and_the_linter_banner_appears(settings):
    build_release(settings, extra=[with_tool()], grades={"acme/orders-mcp": "C"})
    html = client(settings).get("/b/tooled-one").text
    assert "grade-C" in html and "not recommended" in html and "fails today" in html and "tool-not-eligible" in html
    assert "approval #7" in html and "blueprints.draft@2" in html


def test_a_tool_graded_b_is_recommended_with_no_banner(settings):
    build_release(settings, extra=[with_tool()], grades={"acme/orders-mcp": "B"})
    html = client(settings).get("/b/tooled-one").text
    assert "grade-B" in html and "recommended" in html and "not recommended" not in html and "fails today" not in html


def test_a_queued_refresh_and_stale_prices_are_flagged_without_hiding_the_blueprint(settings):
    build_release(settings, refresh=[("docs-rag", "pypi:langgraph has breaking API changes")], now=NOW + 120 * 86400)
    html = client(settings).get("/b/docs-rag").text
    assert "A refresh is queued" in html and "pypi:langgraph has breaking API changes" in html
    assert "Prices may be out of date" in html


def test_stored_text_is_escaped_on_the_detail_page(settings):
    evil = {**good(), "id": "evil-one", "title": EVIL, "summary": EVIL}
    evil["components"][3].pop("tool_ref")
    evil["components"][0]["name"] = EVIL
    evil["deployment"]["notes"] = EVIL
    build_release(settings, extra=[evil])
    html = client(settings).get("/b/evil-one").text
    assert EVIL not in html and "&lt;script&gt;" in html


@pytest.mark.parametrize("path", ["/b/nope", "/b/..%2Fsecret", "/b/UPPER", "/b/" + "a" * 80, "/b/x%00y"])
def test_unknown_or_odd_ids_are_404_not_500(settings, path):
    build_release(settings)
    assert client(settings).get(path).status_code == 404


def test_detail_without_any_release_is_404(settings):
    assert client(settings).get("/b/docs-rag").status_code == 404


def test_patterns_page_lists_the_catalog_with_its_review_status(settings):
    build_release(settings)
    html = client(settings).get("/patterns").text
    assert "Prompt chaining" in html and "When to use" in html and "draft" in html


def test_patterns_page_is_empty_not_broken_without_a_release(settings):
    assert "No patterns published yet" in client(settings).get("/patterns").text


def test_detail_offers_the_starter_with_pinned_version_and_download_links(settings):
    from blueprints.starter import REQUIRED_SYMBOLS
    from core.publish import publish
    conn = connect(settings.db_path)
    store.ensure_schema(conn)
    store.seed(conn, 1.0)
    from blueprints import living as living_module
    surface = {"pypi:langgraph": {"version": "1.3.0", "symbols": set(REQUIRED_SYMBOLS)}}
    from blueprints.publishing import publish_library as publish_with
    publish_with(conn, settings.data_root, NOW, grades={}, packages=PACKAGES, surface=surface)
    conn.close()
    c = client(settings)
    html = c.get("/b/docs-rag").text
    assert "Runnable starter" in html and "langgraph==1.3.0" in html and 'href="/b/docs-rag/starter/app.py"' in html
    r = c.get("/b/docs-rag/starter/app.py")
    assert r.status_code == 200 and "attachment" in r.headers["content-disposition"] and "StateGraph" in r.text
    assert c.get("/b/docs-rag/starter/requirements.txt").text.strip() == "langgraph==1.3.0"
    assert c.get("/b/docs-rag/starter/evil.py").status_code == 404 and c.get("/b/nope/starter/app.py").status_code == 404


def test_without_radar_surface_the_page_says_why_there_is_no_starter(settings):
    build_release(settings)
    c = client(settings)
    html = c.get("/b/docs-rag").text
    assert "No starter is offered" in html and "Runnable starter" in html
    assert c.get("/b/docs-rag/starter/app.py").status_code == 404
