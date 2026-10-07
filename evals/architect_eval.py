# evals/architect_eval.py
"""Architect launch gate (spec §1): 8 fixed cases across risk levels, run end to end against the real models with the
ARCHITECT_* settings from the environment. Records pass rate, paid cost and time per case. Spends real money: run by
hand (`python -m evals.architect_eval`), never from the test suite. Two cases run at a time, like production.
Gate: >= 7/8 pass, mean paid cost <= $0.15, mean time <= 600 s (DeepSeek and GLM only, no free model)."""
import statistics
import sys
import tempfile
import time
from concurrent.futures import ThreadPoolExecutor

from blueprints.architect import clarify, runs
from blueprints.cost import load_pricing
from blueprints.patterns import load_patterns
from core.agent.gateway import chat_factory
from core.db import connect

CASES = [
    "A support agent for an online shop that answers order questions from help articles and issues refunds up to $200.",
    "Reviews supplier contracts for a legal team, flags risky clauses and suggests edits; lawyers make every decision.",
    "Every night, reads new papers on a topic and writes a short digest with links for an engineering team.",
    "Triages incoming bug reports, labels them, finds duplicates and assigns an owner in our tracker.",
    "Answers employees' HR policy questions from the handbook; never sees salary or medical data.",
    "Reconciles supplier invoices against purchase orders and prepares payments for a finance manager to approve.",
    "A coding assistant that proposes pull requests for small dependency upgrades; a maintainer merges.",
    "Summarises patient intake forms for nurses before appointments at a clinic.",
]


def run_case(db_path: str, work, text: str) -> tuple:
    conn = connect(db_path)                              # one connection per thread
    t = time.monotonic()
    rid = runs.STORE.create_run(conn, {"phase": "clarify", "description": text, "consent": True}, "eval", "eval", time.time())
    emit = lambda type, payload: runs.STORE.add_event(conn, rid, type, payload, time.time())  # noqa: E731
    status, result = work(conn, rid, emit)
    if status == "waiting":                              # take the suggested answers, like a visitor who skips
        profile, assumptions = clarify.apply_answers(result["profile"], result["known"], result["questions"], {})
        inp = {"phase": "design", "consent": True, "title": result["title"], "use_case": result["use_case"],
               "profile": profile, "assumptions": assumptions}
        runs.STORE.finish(conn, rid, "waiting", result, None, time.time())
        runs.STORE.requeue(conn, rid, inp)
        status, result = work(conn, rid, emit)
    run = runs.STORE.get_run(conn, rid)
    row = (status, run["cost_usd"], time.monotonic() - t, result.get("reason", ""), text)
    print(f"{status:10} ${row[1]:.4f} {row[2]:6.0f}s  {row[3][:70]} | {text[:50]}", flush=True)
    conn.close()
    return row


def main() -> int:
    cfg = runs.load_config()
    if not cfg.enabled:
        print("ARCHITECT_ENABLED=1, OPENROUTER_API_KEY and ARCHITECT_FAST_MODELS are required")
        return 2
    sources = {"lint_context": {"trust_grades": {}, "frameworks": set(), "models": set(load_pricing()["models"])},
               "pricing": load_pricing(), "index_rows": [], "entry_for": lambda i: None, "patterns": load_patterns()}
    work = runs.make_work(lambda: cfg, chat_factory, lambda: sources)
    with tempfile.TemporaryDirectory() as tmp:
        db_path = f"{tmp}/eval.db"
        conn = connect(db_path)
        runs.STORE.ensure_schema(conn)
        conn.close()
        with ThreadPoolExecutor(max_workers=2) as pool:
            rows = list(pool.map(lambda text: run_case(db_path, work, text), CASES))
    passed = sum(1 for r in rows if r[0] == "passed")
    cost, secs = statistics.mean(r[1] for r in rows), statistics.mean(r[2] for r in rows)
    print(f"passed {passed}/{len(rows)} · mean paid ${cost:.4f} · mean {secs:.0f}s", flush=True)
    ok = passed >= 7 and cost <= 0.15 and secs <= 600
    print("GATE " + ("PASS" if ok else "FAIL"), flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
