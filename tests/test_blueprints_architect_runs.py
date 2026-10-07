# tests/test_blueprints_architect_runs.py
import time

import pytest

pytest.importorskip("langgraph")

from blueprints.architect import runs  # noqa: E402
from blueprints.architect.clarify import DEFAULTS  # noqa: E402
from blueprints.cost import load_pricing  # noqa: E402
from core.agent.config import AgentConfig  # noqa: E402
from core.db import connect  # noqa: E402
from tests.agent_fakes import scripted_factory  # noqa: E402
from tests.blueprint_fixtures import CONTEXT, reply  # noqa: E402

CFG = AgentConfig(enabled=True, fast_models=["paid"], strong_models=["strong"], public_models=["free"], run_usd=0.08,
                  daily_usd=0.25, run_tokens=10_000_000, run_tool_calls=1, run_seconds=60, free_calls_day=900)
SOURCES = {"lint_context": CONTEXT, "pricing": load_pricing(), "index_rows": [], "entry_for": lambda i: None, "patterns": []}


@pytest.fixture
def conn(tmp_path):
    c = connect(str(tmp_path / "a.db"))
    runs.STORE.ensure_schema(c)
    yield c
    c.close()


def test_free_models_come_first_only_with_consent():
    assert runs.roles(CFG, True) == {"bulk": ["free", "paid"], "review": ["strong"]}
    assert runs.roles(CFG, False) == {"bulk": ["paid"], "review": ["strong"]}


def test_the_free_model_is_dropped_once_todays_free_calls_are_used(conn):
    rid = runs.STORE.create_run(conn, {}, "new", "ip", time.time())
    for _ in range(3):
        runs.STORE.add_event(conn, rid, "usage", {"model": "free"}, time.time())
    cfg = AgentConfig(**{**CFG.__dict__, "free_calls_day": 3})
    gw = runs.make_gateway(cfg, conn, rid, lambda *a: None, scripted_factory({}), True)
    assert gw.models_by_role["bulk"] == ["paid"]


def test_the_run_budget_counts_what_the_clarify_phase_already_spent(conn):
    rid = runs.STORE.create_run(conn, {}, "new", "ip", time.time())
    gw = runs.make_gateway(CFG, conn, rid, lambda *a: None, scripted_factory({}), True, spent=(0.05, 100))
    assert gw.budget.usd == 0.05 and gw.budget.max_usd == 0.08


def test_clarify_with_open_questions_waits_and_stores_them(conn):
    rid = runs.STORE.create_run(conn, {"phase": "clarify", "description": "An agent", "consent": True}, "new", "ip", time.time())
    extracted = {"title": "Helper", "use_case": "support", "profile": {}, "stated": []}
    work = runs.make_work(lambda: CFG, lambda cfg: scripted_factory({"free": [reply(extracted)]}), lambda: SOURCES)
    status, result = work(conn, rid, lambda *a: None)
    assert status == "waiting" and 1 <= len(result["questions"]) <= 4 and result["profile"] == DEFAULTS


def test_error_text_never_shows_provider_details():
    assert "boom" not in runs.error_text(RuntimeError("boom from provider"))


def test_old_runs_are_deleted_by_the_expiry_job(tmp_path):
    from blueprints.jobs import ARCHITECT_EXPIRE_JOB, build_job_types
    c = connect(str(tmp_path / "j.db"))
    runs.STORE.ensure_schema(c)
    old = runs.STORE.create_run(c, {}, "new", "ip", time.time() - 8 * 86400)
    types = build_job_types(c, use_llm=False, data_root=str(tmp_path), briefs=[])
    _, stage = types[ARCHITECT_EXPIRE_JOB].stages[0]
    stage(None, {})
    assert runs.STORE.get_run(c, old) is None


def test_the_run_time_budget_is_realistic_and_configurable():
    assert runs.load_config({}).run_seconds >= 1500
    assert runs.load_config({"ARCHITECT_RUN_SECONDS": "2400"}).run_seconds == 2400
