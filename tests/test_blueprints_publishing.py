import json

import pytest

from blueprints import store
from blueprints.cost import load_pricing
from blueprints.linter import lint
from blueprints.publishing import DATASET, build_files, publish_library
from core import review
from core.publish import load_current
from tests.blueprint_fixtures import good

NOW = 1_790_877_600.0
PACKAGES = {"pypi:langgraph": {"latest_stable": "1.2.12", "advisories": [], "last_breaking": None}}


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def release(tmp_path):
    return load_current(tmp_path, "blueprints", DATASET)


def test_only_approved_blueprints_are_published_with_everything_pages_need(db, tmp_path):
    store.seed(db, 1.0)
    pending = {**good(), "id": "pending-one"}
    store.save(db, pending, lint(pending), "generated", "new", 2.0)
    result = publish_library(db, tmp_path, NOW, grades={}, packages=PACKAGES)
    assert result["published"] is True and result["count"] == 4
    r = release(tmp_path)
    assert set(r.manifest["files"]) == {"index.json", "patterns.json", "methodology.json", "blueprints/support-agent.json",
                                        "blueprints/docs-rag.json", "blueprints/code-review-agent.json",
                                        "blueprints/dispute-handling.json"}
    index = json.loads(r.read("index.json"))
    assert index["count"] == 4 and [b["id"] for b in index["blueprints"]] == sorted(b["id"] for b in index["blueprints"])
    entry = json.loads(r.read("blueprints/docs-rag.json"))
    assert entry["topology"] == "workflow" and entry["risk"] == "low" and entry["lint"]["ok"] is True
    assert entry["diagram"].startswith("flowchart LR") and entry["decision"]["topology"] == "workflow"
    assert entry["cost"]["complete"] is True and entry["cost"]["monthly_usd"] > 0 and entry["body"]["id"] == "docs-rag"
    assert entry["living"] == {"tools": [], "frameworks": []} and entry["refresh_pending"] is None


def test_a_rerun_with_nothing_new_publishes_nothing(db, tmp_path):
    store.seed(db, 1.0)
    first = publish_library(db, tmp_path, NOW, grades={}, packages=PACKAGES)
    again = publish_library(db, tmp_path, NOW + 3600, grades={}, packages=PACKAGES)
    assert again == {"published": False, "release": first["release"], "count": 4}


def test_approving_a_draft_publishes_it_on_the_next_run(db, tmp_path):
    store.seed(db, 1.0)
    publish_library(db, tmp_path, NOW, grades={}, packages=PACKAGES)
    body = {**good(), "id": "extra-one"}
    body["components"][3].pop("tool_ref")
    store.save(db, body, lint(body, {"models": set(load_pricing()["models"])}), "generated", "new", 2.0)
    store.request_approval(db, store.get(db, "extra-one", 1), 2.0)
    assert publish_library(db, tmp_path, NOW + 60, grades={}, packages=PACKAGES)["published"] is False       # not approved yet
    review.decide(db, review.entry_for(db, store.APPROVAL_KIND, "extra-one@1")["id"], "approved", now=NOW + 70)
    result = publish_library(db, tmp_path, NOW + 120, grades={}, packages=PACKAGES)
    assert result["published"] is True and result["count"] == 5
    assert "blueprints/extra-one.json" in release(tmp_path).manifest["files"]


def test_living_links_flag_a_tool_that_fell_below_b_and_a_pending_refresh_without_hiding_the_blueprint(db, tmp_path):
    body = good()
    store.save(db, body, lint(body), "seed", "seed", 1.0, status="approved")
    store.add_refresh(db, "refund-support", "acme/orders-mcp is now graded C (was B)", 5, NOW)
    publish_library(db, tmp_path, NOW, grades={"acme/orders-mcp": "C"}, packages=PACKAGES)
    entry = json.loads(release(tmp_path).read("blueprints/refund-support.json"))
    assert entry["living"]["tools"] == [{"component": "orders-tool", "tool_ref": "acme/orders-mcp", "grade": "C", "recommended": False}]
    assert entry["lint"]["ok"] is True and entry["lint_now"]["ok"] is False
    assert {v["rule"] for v in entry["lint_now"]["violations"]} == {"tool-not-eligible"}
    assert entry["refresh_pending"] == {"reason": "acme/orders-mcp is now graded C (was B)", "since": NOW}
    index = json.loads(release(tmp_path).read("index.json"))["blueprints"][0]
    assert index["refresh_pending"] is True and index["lint_ok_now"] is False


def test_the_newest_approved_version_wins(db, tmp_path):
    v1 = good()
    v1["components"][3].pop("tool_ref")
    store.save(db, v1, lint(v1), "seed", "seed", 1.0, status="approved")
    v2 = {**v1, "version": 2, "title": "Refund support agent v2"}
    store.save(db, v2, lint(v2), "generated", "refresh", 2.0, status="approved", parent_version=1)
    publish_library(db, tmp_path, NOW, grades={}, packages={})
    assert json.loads(release(tmp_path).read("blueprints/refund-support.json"))["title"] == "Refund support agent v2"


def test_methodology_documents_the_rules_the_sources_and_staleness(db, tmp_path):
    publish_library(db, tmp_path, NOW, grades={}, packages={})
    m = json.loads(release(tmp_path).read("methodology.json"))
    assert m["owasp"]["version"] == "2025" and m["owasp"]["items"]["LLM06"] == "Excessive Agency"
    assert "unmitigated-capability" in m["lint_rules"] and "interactive-latency" in m["decision_rules"]
    assert m["pricing"]["as_of"] == "2026-10-01" and m["pricing"]["stale"] is False and m["pricing"]["source"].startswith("https://")
    assert m["pricing"]["models"]["claude-haiku-4-5"] == {"label": "Claude Haiku 4.5", "input_per_mtok": 1.0, "output_per_mtok": 5.0}
    patterns = json.loads(release(tmp_path).read("patterns.json"))
    assert len(patterns) == 11 and all(p["status"] == "draft" for p in patterns)
    assert publish_library(db, tmp_path, NOW + 90 * 86400, grades={}, packages={})["published"] is True      # pricing went stale
    assert json.loads(release(tmp_path).read("methodology.json"))["pricing"]["stale"] is True


def test_build_files_is_deterministic(db):
    store.seed(db, 1.0)
    a = build_files(db, NOW, {}, PACKAGES, load_pricing())
    assert a == build_files(db, NOW, {}, PACKAGES, load_pricing())


def test_one_unpublishable_row_is_skipped_and_reported_without_blocking_the_rest(db, tmp_path):
    store.seed(db, 1.0)
    body = good()
    store.save(db, body, lint(body), "generated", "new", 2.0, status="approved")
    broken = good()
    broken["flow"][0]["input_tokens"] = "600"                 # a stored body the cost arithmetic cannot handle
    db.execute("UPDATE blueprints SET body=? WHERE id='refund-support'", (json.dumps(broken),))
    result = publish_library(db, tmp_path, NOW, grades={}, packages=PACKAGES)
    assert result["published"] is True and result["count"] == 4
    r = release(tmp_path)
    index = json.loads(r.read("index.json"))
    assert [s["id"] for s in index["skipped"]] == ["refund-support"] and index["skipped"][0]["error"].startswith("TypeError")
    assert "blueprints/refund-support.json" not in r.manifest["files"]


def test_published_entries_carry_their_provenance(db, tmp_path):
    store.seed(db, 1.0)
    publish_library(db, tmp_path, NOW, grades={}, packages=PACKAGES)
    assert json.loads(release(tmp_path).read("blueprints/docs-rag.json"))["provenance"] == {"source": "seed"}


def test_an_approved_draft_whose_tool_fell_below_b_is_held_not_published(db, tmp_path):
    store.seed(db, 1.0)
    body = good()
    store.save(db, body, lint(body), "generated", "new", 2.0)
    store.request_approval(db, store.get(db, "refund-support", 1), 2.0)
    review.decide(db, review.entry_for(db, store.APPROVAL_KIND, "refund-support@1")["id"], "approved", now=3.0)
    result = publish_library(db, tmp_path, NOW, grades={"acme/orders-mcp": "C"}, packages=PACKAGES)
    assert result["count"] == 4 and store.get(db, "refund-support", 1)["status"] == "draft"
    assert "blueprints/refund-support.json" not in release(tmp_path).manifest["files"]


def test_index_entries_carry_the_requirements_profile_for_the_finder(db, tmp_path):
    store.seed(db, 1.0)
    publish_library(db, tmp_path, NOW, grades={}, packages=PACKAGES)
    index = json.loads(release(tmp_path).read("index.json"))
    assert all(b["profile"]["latency"] in ("interactive", "near_real_time", "batch") for b in index["blueprints"])
