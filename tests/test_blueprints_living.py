import json

import pytest

from blueprints import living, store
from blueprints.linter import lint
from core import jobs, review
from core.events import consume, emit
from core.publish import publish
from tests.blueprint_fixtures import good

NOW = 1_790_877_600.0


@pytest.fixture
def db(conn):
    store.ensure_schema(conn)
    return conn


def publish_trust(root, servers, **extra):
    publish(root, "trust", "directory", {"index.json": json.dumps({"count": len(servers), "servers": servers, **extra}).encode()}, {})


def publish_radar(root, packages):
    publish(root, "radar", "digests", {"packages.json": json.dumps({"generated_at": NOW, "packages": packages}).encode(),
                                       "index.json": b'{"digests": []}'}, {})


def approve(db, body, version=1):
    body = {**body, "version": version}
    store.save(db, body, lint(body), "seed", "seed", 1.0, status="approved")
    return body


def framework_blueprint():
    bp = good()
    bp["components"][1]["framework_ref"] = "pypi:langgraph"
    return bp


def test_trust_grades_come_from_the_published_directory_and_only_graded_servers_count(tmp_path):
    publish_trust(tmp_path, [{"repo": "acme/orders-mcp", "grade": "B", "stale": False}, {"repo": "acme/x", "grade": None},
                             {"repo": "acme/y", "grade": "Z"}, "junk", {"grade": "A"}])
    assert living.load_trust_grades(tmp_path) == {"acme/orders-mcp": "B"}


def test_missing_corrupt_or_odd_published_data_gives_empty_answers(tmp_path):
    assert living.load_trust_grades(tmp_path) == {} and living.load_radar_packages(tmp_path) == {}
    publish(tmp_path, "trust", "directory", {"index.json": b"not json"}, {})
    publish(tmp_path, "radar", "digests", {"packages.json": b'{"packages": [1, 2]}', "index.json": b"{}"}, {})
    assert living.load_trust_grades(tmp_path) == {} and living.load_radar_packages(tmp_path) == {}
    ctx = living.context(tmp_path)
    assert ctx["trust_grades"] == {} and ctx["frameworks"] == set() and "claude-haiku-4-5" in ctx["models"]


def test_context_feeds_the_linter_so_unknown_or_ungraded_references_are_errors(tmp_path):
    publish_trust(tmp_path, [{"repo": "acme/orders-mcp", "grade": "C"}])
    publish_radar(tmp_path, {"pypi:langgraph": {"latest_stable": "1.2.12", "advisories": [], "last_breaking": None}})
    bad = lint(framework_blueprint(), living.context(tmp_path))
    assert {v["rule"] for v in bad["violations"]} == {"tool-not-eligible"}
    publish_trust(tmp_path, [{"repo": "acme/orders-mcp", "grade": "A"}])
    assert lint(framework_blueprint(), living.context(tmp_path))["ok"] is True


def test_snapshot_recommends_only_a_and_b_tools_and_reports_framework_facts():
    bp = framework_blueprint()
    packages = {"pypi:langgraph": {"latest_stable": "1.2.12", "last_breaking": {"from": "1.2.10", "to": "1.2.11"},
                                   "advisories": [{"id": "GHSA-1", "severity": "high", "published": 1.0}]}}
    snap = living.snapshot(bp, {"acme/orders-mcp": "B"}, packages)
    assert snap["tools"] == [{"component": "orders-tool", "tool_ref": "acme/orders-mcp", "grade": "B", "recommended": True}]
    assert snap["frameworks"] == [{"component": "answer-model", "framework_ref": "pypi:langgraph", "tracked": True,
                                   "latest_stable": "1.2.12", "last_breaking": {"from": "1.2.10", "to": "1.2.11"},
                                   "advisories": [{"id": "GHSA-1", "severity": "high", "published": 1.0}]}]
    c = living.snapshot(bp, {"acme/orders-mcp": "C"}, {})
    assert c["tools"][0]["recommended"] is False and c["frameworks"][0]["tracked"] is False
    assert living.snapshot(bp, {}, {})["tools"][0]["grade"] is None
    assert living.references(bp) == {"tools": {"acme/orders-mcp"}, "packages": {"pypi:langgraph"}}


def test_a_grade_drop_below_b_requests_one_refresh_and_a_regeneration_job(db):
    approve(db, framework_blueprint())
    event = emit(db, "trust.grade_changed", {"server_id": "acme/orders-mcp", "old_grade": "B", "new_grade": "C"}, now=NOW)
    result = living.react(db, NOW)
    assert result["events"] == 1
    assert result["refreshes"] == [{"id": "refund-support", "reason": "acme/orders-mcp is now graded C (was B)",
                                    "cause_event_id": event}]
    assert store.pending_refresh(db) == {"refund-support": {"reason": "acme/orders-mcp is now graded C (was B)", "since": NOW}}
    heard = []
    consume(db, "probe", lambda e: heard.append(e.payload), types=["blueprint.needs_refresh"])
    assert heard == [{"blueprint_id": "refund-support", "reason": "acme/orders-mcp is now graded C (was B)", "cause_event_id": event}]
    job = db.execute("SELECT * FROM jobs WHERE job_type=?", (living.GENERATE_JOB,)).fetchone()
    assert job["key"] == f"refresh:refund-support:{event}" and job["planet"] == "blueprints" and job["priority"] == 3.0
    assert json.loads(job["payload"]) == {"id": "refund-support", "reason": "acme/orders-mcp is now graded C (was B)",
                                          "cause_event_id": event}


def test_improvements_and_unrelated_events_do_nothing(db):
    approve(db, framework_blueprint())
    emit(db, "trust.grade_changed", {"server_id": "acme/orders-mcp", "old_grade": "C", "new_grade": "B"}, now=NOW)
    emit(db, "trust.grade_changed", {"server_id": "other/server", "old_grade": "B", "new_grade": "F"}, now=NOW)
    emit(db, "radar.breaking_change_detected", {"package": "pypi:unrelated", "from_version": "1", "to_version": "2", "symbols": []}, now=NOW)
    emit(db, "trust.audit_published", {"server_id": "acme/orders-mcp"}, now=NOW)
    assert living.react(db, NOW)["refreshes"] == [] and store.pending_refresh(db) == {}
    assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0


def test_a_breaking_change_in_a_referenced_framework_requests_a_refresh(db):
    approve(db, framework_blueprint())
    event = emit(db, "radar.breaking_change_detected",
                 {"package": "pypi:langgraph", "from_version": "1.2.10", "to_version": "1.2.11", "symbols": ["a"]}, now=NOW)
    result = living.react(db, NOW)
    assert result["refreshes"][0]["reason"] == "pypi:langgraph 1.2.10 to 1.2.11 has breaking API changes"
    assert result["refreshes"][0]["cause_event_id"] == event


def test_redelivery_is_idempotent_and_only_approved_blueprints_react(db):
    approve(db, framework_blueprint())
    draft = {**framework_blueprint(), "id": "other-draft", "version": 1}
    store.save(db, draft, lint(draft), "generated", "new", 1.0)                      # a draft is not published, so it never reacts
    emit(db, "trust.grade_changed", {"server_id": "acme/orders-mcp", "old_grade": "B", "new_grade": "D"}, now=NOW)
    assert len(living.react(db, NOW)["refreshes"]) == 1
    db.execute("DELETE FROM consumer_cursors WHERE consumer='blueprints.living'")     # simulate at-least-once redelivery
    assert living.react(db, NOW + 60)["refreshes"] == []
    assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
    assert db.execute("SELECT count(*) FROM events WHERE type='blueprint.needs_refresh'").fetchone()[0] == 1


def test_approving_a_new_version_clears_the_pending_refresh(db):
    v1 = approve(db, framework_blueprint())
    store.add_refresh(db, "refund-support", "tool graded C", 7, NOW)
    v2 = {**v1, "version": 2}
    store.save(db, v2, lint(v2), "generated", "tool graded C", NOW + 1, parent_version=1)
    store.request_approval(db, store.get(db, "refund-support", 2), NOW + 1)
    assert store.sync_decisions(db, NOW + 2) == {"approved": 0, "rejected": 0} and store.pending_refresh(db) != {}
    review.decide(db, review.entry_for(db, store.APPROVAL_KIND, "refund-support@2")["id"], "approved", now=NOW + 3)
    store.sync_decisions(db, NOW + 4)
    assert store.pending_refresh(db) == {}


def test_two_events_for_one_blueprint_queue_a_single_regeneration_job(db):
    approve(db, framework_blueprint())
    emit(db, "trust.grade_changed", {"server_id": "acme/orders-mcp", "old_grade": "B", "new_grade": "C"}, now=NOW)
    emit(db, "radar.breaking_change_detected",
         {"package": "pypi:langgraph", "from_version": "1.2.10", "to_version": "1.2.11", "symbols": []}, now=NOW)
    assert len(living.react(db, NOW)["refreshes"]) == 2
    assert db.execute("SELECT count(*) FROM blueprint_refresh").fetchone()[0] == 2           # both causes are recorded
    assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1                        # one regeneration covers them


def test_an_event_arriving_while_a_regeneration_is_running_queues_the_next_one(db):
    approve(db, framework_blueprint())
    emit(db, "trust.grade_changed", {"server_id": "acme/orders-mcp", "old_grade": "B", "new_grade": "C"}, now=NOW)
    living.react(db, NOW)
    assert jobs.claim(db, "w", {"blueprints": 1.0}, now=NOW) is not None
    emit(db, "radar.breaking_change_detected",
         {"package": "pypi:langgraph", "from_version": "1.2.10", "to_version": "1.2.11", "symbols": []}, now=NOW + 1)
    living.react(db, NOW + 1)
    assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 2


def test_grades_and_packages_can_be_read_from_already_loaded_data():
    index = {"servers": [{"repo": "Acme/Orders-MCP", "grade": "B"}, {"repo": "x/y", "grade": "Z"}, "junk"]}
    assert living.grades_from_index(index) == {"acme/orders-mcp": "B"}
    assert living.grades_from_index("junk") == {} and living.grades_from_index({"servers": 3}) == {}
    assert living.packages_from({"packages": {"pypi:a": {}}}) == {"pypi:a": {}}
    assert living.packages_from({"packages": [1]}) == {} and living.packages_from(None) == {}


def test_a_failed_enqueue_leaves_no_pending_refresh_and_the_event_is_retried(db, monkeypatch):
    approve(db, framework_blueprint())
    emit(db, "trust.grade_changed", {"server_id": "acme/orders-mcp", "old_grade": "B", "new_grade": "C"}, now=NOW)

    def boom(*args, **kwargs):
        raise RuntimeError("database is locked")
    monkeypatch.setattr(living.jobs, "enqueue", boom)
    with pytest.raises(RuntimeError):
        living.react(db, NOW)
    assert store.pending_refresh(db) == {}
    assert db.execute("SELECT count(*) FROM events WHERE type='blueprint.needs_refresh'").fetchone()[0] == 0
    monkeypatch.undo()
    assert len(living.react(db, NOW + 1)["refreshes"]) == 1
    assert db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 1
