"""Registers Blueprints job types and schedules with the worker (PLANET_MODULES=trust.jobs,radar.jobs,blueprints.jobs)."""
import os
import time
from datetime import datetime

from blueprints import generate, living, store
from blueprints.cost import load_pricing
from blueprints.living import GENERATE_JOB
from blueprints.publishing import publish_library
from core.claude_cli import ClaudeCliBackend  # noqa: F401  (kept importable for callers and tests)
from core.control.batch import make_backend
from core.jobs import enqueue
from core.periodic import schedule
from core.scheduler import IST, GuardedBackend, in_night_window
from core.worker import JobType

REACT_JOB, REACT_EVERY_S = "blueprints.react", 1800
PUBLISH_JOB, PUBLISH_EVERY_S = "blueprints.publish", 1800
NIGHTLY_JOB = "blueprints.nightly"
ARCHITECT_EXPIRE_JOB, ARCHITECT_EXPIRE_EVERY_S = "blueprints.architect_expire", 6 * 3600


def night_key(now: float) -> str:
    """The IST date of now + 7h: the whole 23:00-06:00 night is named for the morning it ends in."""
    return datetime.fromtimestamp(now + 7 * 3600, IST).date().isoformat()


def schedule_periodic(conn, now: float) -> None:
    """Worker hook: idempotent per window, so calling it every loop is cheap."""
    store.ensure_schema(conn)
    schedule(conn, "blueprints", REACT_JOB, REACT_EVERY_S, now=now)
    schedule(conn, "blueprints", PUBLISH_JOB, PUBLISH_EVERY_S, now=now)
    schedule(conn, "blueprints", ARCHITECT_EXPIRE_JOB, ARCHITECT_EXPIRE_EVERY_S, now=now)
    if in_night_window(now):
        enqueue(conn, "blueprints", NIGHTLY_JOB, f"night:{night_key(now)}", {}, now=now)


def build_job_types(conn, backend=None, use_llm: bool = True, data_root=None, briefs=None, pricing=None,
                    context_loader=None, clock=time.time) -> dict[str, JobType]:
    store.ensure_schema(conn)
    store.seed(conn, clock())
    data_root = data_root or os.environ.get("DATA_ROOT", "data/published")
    briefs = store.load_briefs() if briefs is None else briefs
    pricing = pricing or load_pricing()
    context_loader = context_loader or (lambda: living.context(data_root))
    if not use_llm:
        backend = None
    elif backend is None:
        backend = make_backend(conn, os.environ, "BLUEPRINTS_MODEL")      # OpenRouter under the daily cap, else the Claude CLI

    def pick(job, out):
        brief = store.next_brief(conn, briefs)
        if brief is None:
            return {"picked": None}
        enqueue(conn, "blueprints", GENERATE_JOB, f"new:{brief['id']}",
                {"id": brief["id"], "reason": "new", "cause_event_id": None}, priority=1.0, now=clock())
        return {"picked": brief["id"]}

    def expire_architect(job, out):
        from blueprints.architect import runs as architect_runs      # imported here so the worker needs the agent stack only for this job
        architect_runs.STORE.ensure_schema(conn)
        architect_runs.STORE.expire(conn, clock())
        architect_runs.STORE.expire_waiting(conn, clock(), architect_runs.WAIT_SECONDS)
        return {"expired": True}

    return {
        ARCHITECT_EXPIRE_JOB: JobType("blueprints", [("expire", expire_architect)]),
        GENERATE_JOB: JobType("blueprints", generate.stages(conn, backend=backend, briefs=briefs,
                                                            context_loader=context_loader, pricing=pricing, clock=clock)),
        REACT_JOB: JobType("blueprints", [("react", lambda job, out: living.react_all(conn, clock(), data_root))]),
        PUBLISH_JOB: JobType("blueprints", [("publish", lambda job, out: publish_library(conn, data_root, clock(), pricing))]),
        NIGHTLY_JOB: JobType("blueprints", [("pick", pick)]),
    }
