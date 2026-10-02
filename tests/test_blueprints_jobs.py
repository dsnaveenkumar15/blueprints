import json

import pytest

from blueprints import store
from blueprints.cost import load_pricing
from blueprints.jobs import (GENERATE_JOB, NIGHTLY_JOB, PUBLISH_JOB, REACT_JOB, build_job_types, night_key,
                             schedule_periodic)
from core.events import emit
from core.jobs import enqueue
from core.llm import FakeBackend
from core.publish import load_current
from core.worker import load_registry, load_schedulers, run_once
from tests.blueprint_fixtures import context, good

NOW = 1_790_800_000.0
NIGHT = 1_790_877_600.0                  # 2026-10-01 23:30 IST
DAY = NIGHT - 12 * 3600


def count(conn, job_type):
    return conn.execute("SELECT count(*) FROM jobs WHERE job_type=?", (job_type,)).fetchone()[0]


def types(conn, tmp_path, backend=None, **over):
    return build_job_types(conn, backend=backend, use_llm=backend is not None, data_root=tmp_path,
                           context_loader=context, clock=lambda: NOW, **over)


def test_night_key_is_the_ist_morning_the_night_ends_in():
    assert night_key(NIGHT) == "2026-10-02" and night_key(NIGHT + 5 * 3600) == "2026-10-02"


def test_periodic_react_and_publish_always_and_one_nightly_pick_only_at_night(conn):
    schedule_periodic(conn, DAY)
    schedule_periodic(conn, DAY + 60)
    assert [count(conn, j) for j in (REACT_JOB, PUBLISH_JOB, NIGHTLY_JOB)] == [1, 1, 0]
    schedule_periodic(conn, NIGHT)
    schedule_periodic(conn, NIGHT + 600)
    assert count(conn, NIGHTLY_JOB) == 1
    assert conn.execute("SELECT key FROM jobs WHERE job_type=?", (NIGHTLY_JOB,)).fetchone()["key"] == "night:2026-10-02"


def test_the_worker_discovers_the_module_and_seeds_the_library(conn, tmp_path):
    registry = load_registry(["blueprints.jobs"], conn)
    assert set(registry) == {GENERATE_JOB, REACT_JOB, PUBLISH_JOB, NIGHTLY_JOB}
    assert [name for name, _ in registry[GENERATE_JOB].stages] == ["prepare", "refine", "record"]
    assert [s.__module__ for s in load_schedulers(["blueprints.jobs"])] == ["blueprints.jobs"]
    assert len(store.approved(conn)) == 4


def test_the_publish_job_syncs_approvals_and_publishes(conn, tmp_path):
    t = types(conn, tmp_path)
    enqueue(conn, "blueprints", PUBLISH_JOB, "k", {}, now=NOW)
    assert run_once(conn, t, {"blueprints": 1.0}, "w", clock=lambda: NOW) == "done"
    assert len(json.loads(load_current(tmp_path, "blueprints", "library").read("index.json"))["blueprints"]) == 4


def test_the_react_job_turns_a_grade_drop_into_a_regeneration_job(conn, tmp_path):
    t = types(conn, tmp_path)
    body = good()
    store.save(conn, body, {"ok": True, "errors": 0, "warnings": 0, "violations": []}, "seed", "seed", 1.0, status="approved")
    emit(conn, "trust.grade_changed", {"server_id": "acme/orders-mcp", "old_grade": "B", "new_grade": "C"}, now=NOW)
    enqueue(conn, "blueprints", REACT_JOB, "k", {}, now=NOW)
    assert run_once(conn, t, {"blueprints": 1.0}, "w", clock=lambda: NOW) == "done"
    assert count(conn, GENERATE_JOB) == 1


def test_the_nightly_job_enqueues_the_next_brief_and_does_nothing_when_the_library_is_complete(conn, tmp_path):
    t = types(conn, tmp_path)
    enqueue(conn, "blueprints", NIGHTLY_JOB, "night:a", {}, now=NOW)
    assert run_once(conn, t, {"blueprints": 1.0}, "w", clock=lambda: NOW) == "done"
    job = conn.execute("SELECT * FROM jobs WHERE job_type=?", (GENERATE_JOB,)).fetchone()
    first = store.load_briefs()[0]["id"]
    assert job["key"] == f"new:{first}" and json.loads(job["payload"]) == {"id": first, "reason": "new", "cause_event_id": None}
    conn.execute("DELETE FROM jobs WHERE job_type=?", (GENERATE_JOB,))
    exhausted = types(conn, tmp_path, briefs=[])
    enqueue(conn, "blueprints", NIGHTLY_JOB, "night:b", {}, now=NOW)
    assert run_once(conn, exhausted, {"blueprints": 1.0}, "w", clock=lambda: NOW) == "done"
    assert count(conn, GENERATE_JOB) == 0


def test_a_generate_job_end_to_end_records_a_draft_for_the_owner(conn, tmp_path):
    brief = {"id": "refund-support", "title": "Refund support agent", "use_case": "customer support", "profile": good()["profile"]}
    backend = FakeBackend([json.dumps({"blueprint": good()}), json.dumps({"issues": [], "summary": "ok"})])
    t = types(conn, tmp_path, backend=backend, briefs=[brief])
    enqueue(conn, "blueprints", GENERATE_JOB, "new:refund-support", {"id": "refund-support", "reason": "new", "cause_event_id": None}, now=NOW)
    assert run_once(conn, t, {"blueprints": 1.0}, "w", clock=lambda: NOW) == "done"
    assert store.get(conn, "refund-support", 1)["status"] == "draft"
