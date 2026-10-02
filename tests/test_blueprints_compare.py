import pytest

from tests.test_blueprints_site import build_release, client, settings  # noqa: F401  (fixture re-export)
from web.blueprints_site import compare_rows, normalise_entry


def entry(**over):
    from tests.test_blueprints_site import good
    body = good()
    base = {"id": "x", "title": "X", "summary": "s", "use_case": "u", "version": 1, "topology": "router", "risk": "high",
            "patterns": ["routing"], "body": body, "decision": {"topology": "router"}, "cost": {"per_request_usd": 0.01, "monthly_usd": 100.0,
            "latency_s": 2.0, "complete": True, "budget_usd_month": 200, "over_budget": False, "steps": [], "pricing": {}}, "lint_now": {"ok": True}}
    return normalise_entry({**base, **over})


def test_rows_mark_exactly_the_differences():
    a, b = entry(), entry(topology="workflow", risk="low")
    rows = {r["label"]: r for r in compare_rows(a, b)}
    assert rows["Topology"] == {"label": "Topology", "a": "router", "b": "workflow", "differs": True}
    assert rows["Use case"]["differs"] is False and rows["Risk"]["differs"] is True
    assert rows["Monthly cost"]["a"] == "$100.00"


def test_incomplete_costs_and_missing_sections_do_not_break_the_rows():
    a = entry(cost={"complete": False, "monthly_usd": None})
    sparse = entry()
    sparse["body"] = {**sparse["body"], "guardrails": None, "threats": None}
    rows = {r["label"]: r for r in compare_rows(a, sparse)}
    assert rows["Monthly cost"]["a"] == "n/a" and rows["Guardrails"]["b"] == "0"


def test_compare_page_shows_both_blueprints_side_by_side(settings):
    build_release(settings)
    html = client(settings).get("/compare?a=support-agent&b=docs-rag").text
    assert "Customer support agent" in html and "Documentation Q&amp;A" in html and "router" in html and "workflow" in html
    assert 'class="differs"' in html and 'href="/b/support-agent"' in html


def test_comparing_a_blueprint_with_itself_shows_no_differences(settings):
    build_release(settings)
    html = client(settings).get("/compare?a=docs-rag&b=docs-rag").text
    assert 'class="differs"' not in html


@pytest.mark.parametrize("query", ["", "?a=docs-rag", "?b=docs-rag", "?a=docs-rag&b=nope", "?a=..%2Fx&b=docs-rag", "?a=docs-rag&b=docs-rag&b=support-agent",
                                   "?a=" + "x" * 80 + "&b=docs-rag", "?a=UPPER&b=docs-rag"])
def test_missing_unknown_or_odd_ids_give_a_chooser_or_404_never_500(settings, query):
    build_release(settings)
    r = client(settings).get("/compare" + query)
    assert r.status_code in (200, 404)


def test_the_chooser_lists_the_published_blueprints(settings):
    build_release(settings)
    html = client(settings).get("/compare").text
    assert 'name="a"' in html and 'name="b"' in html and "docs-rag" in html


def test_the_library_links_to_compare_and_the_nav_has_it(settings):
    build_release(settings)
    html = client(settings).get("/").text
    assert 'action="/compare"' in html and 'href="/compare"' in html


def test_compare_survives_non_list_tools_and_patterns():
    a = entry()
    odd = normalise_entry({**{k: v for k, v in a.items()}, "patterns": 7, "living": {"tools": 5, "frameworks": None}})
    for pair in ((a, odd), (odd, odd)):
        rows = {r["label"]: r for r in compare_rows(*pair)}
        assert rows["Patterns"]["b"] == "" and rows["Tools not graded A or B"]["b"] == "0"
