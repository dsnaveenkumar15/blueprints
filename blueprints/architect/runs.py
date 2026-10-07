# blueprints/architect/runs.py
"""Architect runs: the shared run store under the 'architect' prefix (kept 7 days; a waiting run holds its visitor),
the two phases (clarify, then design) and a gateway per run with its budgets. Free models are used only after the
visitor's consent and below the day's free-call cap; the paid spend of both phases counts toward one run budget."""
import os
import threading
import time

from blueprints.architect import candidates, clarify
from blueprints.architect.graph import STEP_ERRORS, Context, run_design
from blueprints.architect.llm import StepFailed
from core.agent.config import AgentConfig, load_agent_config
from core.agent.gateway import Budget, BudgetExceeded, Gateway
from core.agent.runs import LimitError, RunStore  # noqa: F401

KEEP_DAYS = 7
WAIT_SECONDS = 1800
MESSAGES = {"off": "The architect is switched off right now. Try again later.",
            "budget": "Today's design budget is used up. Designs resume at 00:00 UTC.",
            "one": "You already have a design in progress: one at a time.",
            "daily": "You have used your {n} designs for today.",
            "busy": "The architect is busy. Try again in a few minutes."}
STORE = RunStore("architect", KEEP_DAYS, holding=("queued", "running", "waiting"), interrupted="interrupted", id_bytes=16,
                 messages=MESSAGES)
DEFAULTS = dict(daily_usd=0.25, run_usd=0.20, run_tokens=1_500_000, run_tool_calls=1, run_seconds=900, per_ip_day=3,
                concurrent=2, queue=3, free_calls_day=900)


def load_config(env=os.environ) -> AgentConfig:
    cfg = load_agent_config("ARCHITECT_", env, **DEFAULTS)
    if env.get("ARCHITECT_RUN_SECONDS"):
        cfg.run_seconds = int(env["ARCHITECT_RUN_SECONDS"])
    return cfg


def roles(cfg: AgentConfig, free_ok: bool) -> dict[str, list[str]]:
    return {"bulk": (list(cfg.public_models) if free_ok else []) + list(cfg.fast_models), "review": list(cfg.strong_models)}


def make_gateway(cfg: AgentConfig, conn, run_id: str, emit, factory, consent: bool, spent=(0.0, 0)) -> Gateway:
    free_ok = consent and (not cfg.free_calls_day or STORE.calls_today(conn, time.time(), cfg.public_models) < cfg.free_calls_day)
    totals = {"usd": float(spent[0]), "tokens": int(spent[1])}
    lock = threading.Lock()                       # steps run side by side and share one database connection

    def on_usage(model, provider, usd, tokens):
        with lock:
            totals["usd"] += usd
            totals["tokens"] += tokens
            STORE.add_usage(conn, run_id, usd, tokens)
            emit("usage", {"model": model, "usd": round(usd, 6), "tokens": tokens, "free": model in cfg.public_models,
                           "total_usd": round(totals["usd"], 6), "total_tokens": totals["tokens"]})

    def day_guard():
        with lock:
            spent_today = STORE.day_spend(conn, time.time())
        if spent_today >= cfg.daily_usd:
            raise BudgetExceeded("daily budget reached")

    budget = Budget(cfg.run_usd, cfg.run_tokens, cfg.run_tool_calls, time.monotonic() + cfg.run_seconds)
    budget.add(float(spent[0]), int(spent[1]))
    prices = {**{m: (0.0, 0.0) for m in cfg.public_models}, **cfg.prices}
    return Gateway(roles(cfg, free_ok), factory, prices, budget, on_usage, day_guard)


def error_text(exc: Exception) -> str:
    if isinstance(exc, StepFailed):
        return "The model could not read the description. Please try rephrasing it."
    if isinstance(exc, BudgetExceeded):
        return "Today's design budget is used up. Designs resume at 00:00 UTC."
    return "The design failed. Please try again later."


def design(gw, emit, inp: dict, src: dict) -> tuple[str, dict]:
    profile = inp["profile"]
    if inp.get("instruction"):
        try:
            profile = clarify.reprofile(gw, profile, inp["instruction"])
        except STEP_ERRORS:
            emit("info", {"text": "Kept the requirements as they were: the instruction could not be applied to them"})
    ctx = Context(gateway=gw, emit=emit, lint_context=src["lint_context"], pricing=src["pricing"],
                  index_rows=src["index_rows"], entry_for=src["entry_for"], patterns=src["patterns"])
    result = run_design(ctx, {"brief": candidates.brief(inp["title"], inp["use_case"], profile),
                              "assumptions": inp.get("assumptions") or [], "previous": inp.get("previous"),
                              "instruction": inp.get("instruction")})
    return result["verdict"], result


def make_work(get_config, get_factory, get_sources):
    def work(conn, run_id, emit):
        cfg = get_config()
        run = STORE.get_run(conn, run_id)
        inp = dict(run["input"])
        gw = make_gateway(cfg, conn, run_id, emit, get_factory(cfg), inp.get("consent") is True,
                          spent=(run["cost_usd"], run["tokens"]))
        if inp.get("phase") == "clarify":
            emit("node", {"name": "clarify", "status": "start"})
            c = clarify.clarify(gw, inp["description"])
            emit("node", {"name": "clarify", "status": "end"})
            if c["questions"]:
                emit("questions", {"count": len(c["questions"])})
                return "waiting", c
            profile, assumptions = clarify.apply_answers(c["profile"], c["known"], [], {})
            inp.update(phase="design", title=c["title"], use_case=c["use_case"], profile=profile, assumptions=assumptions)
        return design(gw, emit, inp, get_sources())
    return work
