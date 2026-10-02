import json
import shutil
import sqlite3
from pathlib import Path

import pytest

from blueprints import store
from blueprints.cost import load_pricing
from blueprints.decision import decide
from blueprints.linter import lint
from blueprints.models import Profile
from core import review
from tests.blueprint_fixtures import good

LIBRARY = Path(__file__).resolve().parent.parent / "blueprints" / "library"
SEED_IDS = {"support-agent", "docs-rag", "code-review-agent", "dispute-handling"}


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def models():
    return {"models": set(load_pricing()["models"])}


def test_the_four_seeds_lint_with_no_violations_at_all():
    seeds = store.load_seeds()
    assert {s["id"] for s in seeds} == SEED_IDS and len(seeds) == 4
    for s in seeds:
        assert (LIBRARY / f"{s['id']}.json").exists()
        assert lint(s, models()) == {"ok": True, "errors": 0, "warnings": 0, "violations": []}, s["id"]
        assert s["version"] == 1 and decide(s["profile"])["topology"] == s["topology"], s["id"]


def test_seeds_cover_the_spec_examples_and_use_distinct_shapes():
    seeds = {s["id"]: s for s in store.load_seeds()}
    assert seeds["support-agent"]["topology"] == "router" and seeds["docs-rag"]["topology"] == "workflow"
    assert seeds["code-review-agent"]["topology"] == "orchestrator-workers"
    assert seeds["dispute-handling"]["profile"]["data_sensitivity"] == "regulated"
    for s in seeds.values():
        assert not any(c.get("tool_ref") or c.get("framework_ref") for c in s["components"])   # no invented grades


def test_sixteen_briefs_with_valid_profiles_and_no_overlap_with_the_seeds():
    briefs = store.load_briefs()
    ids = [b["id"] for b in briefs]
    assert len(briefs) == 16 and len(set(ids)) == 16 and not (set(ids) & SEED_IDS)
    for b in briefs:
        Profile.model_validate(b["profile"])
        assert b["title"] and b["use_case"] and decide(b["profile"])["topology"]


def test_seed_loads_approved_and_is_idempotent(db):
    assert sorted(i for i, _ in store.seed(db, 1.0)) == sorted(SEED_IDS)
    assert store.seed(db, 2.0) == []
    rows = store.approved(db)
    assert [r["id"] for r in rows] == sorted(SEED_IDS)
    assert all(r["status"] == "approved" and r["source"] == "seed" and r["lint"]["ok"] for r in rows)
    assert isinstance(rows[0]["body"], dict) and rows[0]["decided_at"] == 1.0


def test_seed_refuses_a_seed_that_fails_the_linter(db, tmp_path):
    shutil.copytree(LIBRARY, tmp_path / "lib")
    bad = json.loads((tmp_path / "lib" / "docs-rag.json").read_text(encoding="utf-8"))
    bad["threats"][0]["owasp"] = "LLM99"
    (tmp_path / "lib" / "docs-rag.json").write_text(json.dumps(bad), encoding="utf-8")
    with pytest.raises(ValueError, match="docs-rag"):
        store.seed(db, 1.0, tmp_path / "lib")


def test_save_get_latest_and_versions(db):
    body = good()
    assert store.next_version(db, "refund-support") == 1
    store.save(db, body, lint(body), "generated", "new", 5.0)
    assert store.next_version(db, "refund-support") == 2
    row = store.get(db, "refund-support", 1)
    assert row["status"] == "draft" and row["body"]["id"] == "refund-support" and row["created_at"] == 5.0
    body2 = {**good(), "version": 2}
    store.save(db, body2, lint(body2), "generated", "refresh", 6.0, cause_event_id=9, parent_version=1)
    assert store.latest(db, "refund-support")["version"] == 2 and store.get(db, "refund-support", 3) is None
    with pytest.raises(sqlite3.IntegrityError):
        store.save(db, body, lint(body), "generated", "again", 7.0)


def test_approval_flow_follows_the_review_queue_and_serves_the_highest_approved_version(db):
    v1, v2 = good(), {**good(), "version": 2}
    store.save(db, v1, lint(v1), "generated", "new", 1.0)
    store.save(db, v2, lint(v2), "generated", "refresh", 2.0, parent_version=1)
    for version in (1, 2):
        store.request_approval(db, store.get(db, "refund-support", version), 3.0)
    assert store.sync_decisions(db, 4.0) == {"approved": 0, "rejected": 0} and store.approved(db) == []
    entry = review.entry_for(db, store.APPROVAL_KIND, "refund-support@1")
    review.decide(db, entry["id"], "approved", now=5.0)
    assert store.sync_decisions(db, 6.0) == {"approved": 1, "rejected": 0}
    assert [r["version"] for r in store.approved(db)] == [1]
    entry2 = review.entry_for(db, store.APPROVAL_KIND, "refund-support@2")
    review.decide(db, entry2["id"], "approved", now=7.0)
    store.sync_decisions(db, 8.0)
    assert [r["version"] for r in store.approved(db)] == [2]               # the newest approved version wins
    assert store.get(db, "refund-support", 1)["status"] == "approved"


def test_a_rejected_decision_never_publishes(db):
    body = good()
    store.save(db, body, lint(body), "generated", "new", 1.0)
    store.request_approval(db, store.get(db, "refund-support", 1), 2.0)
    review.decide(db, review.entry_for(db, store.APPROVAL_KIND, "refund-support@1")["id"], "rejected", now=3.0)
    assert store.sync_decisions(db, 4.0) == {"approved": 0, "rejected": 1}
    assert store.approved(db) == [] and store.get(db, "refund-support", 1)["status"] == "rejected"


def test_approval_request_payload_carries_the_lint_report_and_is_idempotent(db):
    body = good()
    store.save(db, body, lint(body), "generated", "new", 1.0)
    row = store.get(db, "refund-support", 1)
    store.request_approval(db, row, 2.0, open_issues=["reviewer: stale-policy mitigation is weak"])
    store.request_approval(db, row, 3.0)
    entries = review.list_open(db, "blueprints")
    assert len(entries) == 1 and entries[0]["kind"] == "blueprint_approval" and entries[0]["ref"] == "refund-support@1"
    payload = json.loads(entries[0]["payload"])
    assert payload["title"] == "Refund support agent" and payload["lint_ok"] is True
    assert payload["open_issues"] == ["reviewer: stale-policy mitigation is weak"] and payload["version"] == 1


def test_refresh_requests_are_deduplicated_listed_and_resolved(db):
    assert store.add_refresh(db, "refund-support", "tool graded C", 10, 1.0) is True
    assert store.add_refresh(db, "refund-support", "tool graded C", 10, 2.0) is False
    assert store.add_refresh(db, "refund-support", "framework break", 11, 3.0) is True
    assert store.pending_refresh(db) == {"refund-support": {"reason": "framework break", "since": 3.0}}
    store.resolve_refresh(db, "refund-support")
    assert store.pending_refresh(db) == {}


def test_next_brief_skips_briefs_that_already_have_any_blueprint(db):
    briefs = store.load_briefs()
    first = store.next_brief(db, briefs)
    assert first["id"] == briefs[0]["id"]
    body = {**good(), "id": first["id"]}
    store.save(db, body, lint(body), "generated", "new", 1.0, status="rejected")
    assert store.next_brief(db, briefs)["id"] == briefs[1]["id"]
    for b in briefs:
        body = {**good(), "id": b["id"]}
        if store.get(db, b["id"], 1) is None:
            store.save(db, body, lint(body), "generated", "new", 1.0)
    assert store.next_brief(db, briefs) is None


def test_a_lint_clean_body_is_stored_in_its_normalised_form(db):
    body = good()
    body["flow"][0]["input_tokens"] = "600"                  # the schema coerces it, so the stored copy must be the coerced one
    result = lint(body)
    assert result["ok"] is True
    store.save(db, body, result, "generated", "new", 1.0)
    assert store.get(db, "refund-support", 1)["body"]["flow"][0]["input_tokens"] == 600


def test_a_brief_whose_generation_job_died_is_skipped_so_it_cannot_block_the_rest(db):
    from blueprints.living import GENERATE_JOB
    from core import jobs
    briefs = store.load_briefs()
    jobs.enqueue(db, "blueprints", GENERATE_JOB, f"new:{briefs[0]['id']}", {"id": briefs[0]["id"]}, now=1.0)
    job = jobs.claim(db, "w", {"blueprints": 1.0}, now=2.0)
    jobs.fail_job(db, job, "boom", retryable=False, now=3.0)
    assert store.next_brief(db, briefs)["id"] == briefs[1]["id"]


def _approved_draft(db, **save_args):
    body = good()
    store.save(db, body, lint(body), "generated", "new", 1.0, **save_args)
    store.request_approval(db, store.get(db, "refund-support", 1), 2.0)
    entry_id = review.entry_for(db, store.APPROVAL_KIND, "refund-support@1")["id"]
    review.decide(db, entry_id, "approved", now=3.0)
    return entry_id


def test_an_owner_approval_is_held_while_the_draft_fails_the_linter_with_todays_context(db):
    _approved_draft(db)
    bad = {"trust_grades": {"acme/orders-mcp": "C"}, "frameworks": set(), "models": set(load_pricing()["models"])}
    assert store.sync_decisions(db, 4.0, bad) == {"approved": 0, "rejected": 0}
    row = store.get(db, "refund-support", 1)
    assert row["status"] == "draft" and "held at approval" in row["reason"] and "tool-not-eligible" in row["reason"]
    assert store.approved(db) == []
    assert store.sync_decisions(db, 5.0, bad) == {"approved": 0, "rejected": 0}
    assert store.get(db, "refund-support", 1)["reason"].count("held at approval") == 1
    assert store.sync_decisions(db, 6.0, {**bad, "trust_grades": {"acme/orders-mcp": "B"}}) == {"approved": 1, "rejected": 0}
    assert store.get(db, "refund-support", 1)["reason"] == "new"


def test_approval_records_the_review_id_in_the_provenance(db):
    entry_id = _approved_draft(db, provenance={"model": "sonnet"})
    store.sync_decisions(db, 4.0)
    assert store.get(db, "refund-support", 1)["provenance"] == {"model": "sonnet", "approval_id": entry_id, "approved_at": 4.0}


def test_seeds_carry_a_seed_provenance(db):
    store.seed(db, 1.0)
    assert all(r["provenance"] == {"source": "seed"} for r in store.approved(db))


def test_approving_a_draft_clears_only_refreshes_raised_before_it_was_generated(db):
    v1 = good()
    store.save(db, v1, lint(v1), "seed", "seed", 1.0, status="approved")
    store.add_refresh(db, "refund-support", "early", 1, 5.0)
    v2 = {**v1, "version": 2}
    store.save(db, v2, lint(v2), "generated", "early", 10.0, parent_version=1)
    store.add_refresh(db, "refund-support", "late", 2, 20.0)
    store.request_approval(db, store.get(db, "refund-support", 2), 11.0)
    review.decide(db, review.entry_for(db, store.APPROVAL_KIND, "refund-support@2")["id"], "approved", now=30.0)
    store.sync_decisions(db, 31.0)
    assert store.pending_refresh(db) == {"refund-support": {"reason": "late", "since": 20.0}}
