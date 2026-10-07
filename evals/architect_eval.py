# evals/architect_eval.py
"""Architect launch gate (spec §1): 8 fixed cases across risk levels, run end to end against the real models with the
ARCHITECT_* settings from the environment. Records pass rate, paid cost and time per case. Spends real money: run by
hand (`python -m evals.architect_eval`), never from the test suite. Gate: >= 7/8 pass, mean paid cost <= $0.06,
mean time <= 480 s."""
import statistics
import sys
import tempfile
import time

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


def main() -> int:
    cfg = runs.load_config()
    if not cfg.enabled:
        print("ARCHITECT_ENABLED=1, OPENROUTER_API_KEY and ARCHITECT_FAST_MODELS are required")
        return 2
    sources = {"lint_context": {"trust_grades": {}, "frameworks": set(), "models": set(load_pricing()["models"])},
               "pricing": load_pricing(), "index_rows": [], "entry_for": lambda i: None, "patterns": load_patterns()}
    work = runs.make_work(lambda: cfg, chat_factory, lambda: sources)
    rows = []
    with tempfile.TemporaryDirectory() as tmp:
        conn = connect(f"{tmp}/eval.db")
        runs.STORE.ensure_schema(conn)
        for text in CASES:
            t = time.monotonic()
            rid = runs.STORE.create_run(conn, {"phase": "clarify", "description": text, "consent": True}, "eval", "eval", time.time())
            emit = lambda type, payload, rid=rid: runs.STORE.add_event(conn, rid, type, payload, time.time())  # noqa: E731
            status, result = work(conn, rid, emit)
            if status == "waiting":                      # take the suggested answers, like a visitor who skips
                profile, assumptions = clarify.apply_answers(result["profile"], result["known"], result["questions"], {})
                inp = {"phase": "design", "consent": True, "title": result["title"], "use_case": result["use_case"],
                       "profile": profile, "assumptions": assumptions}
                runs.STORE.finish(conn, rid, "waiting", result, None, time.time())
                runs.STORE.requeue(conn, rid, inp)
                status, result = work(conn, rid, emit)
            run = runs.STORE.get_run(conn, rid)
            rows.append((status, run["cost_usd"], time.monotonic() - t, result.get("reason", "")))
            print(f"{status:10} ${run['cost_usd']:.4f} {time.monotonic() - t:6.0f}s  {text[:60]}")
    passed = sum(1 for r in rows if r[0] == "passed")
    cost, secs = statistics.mean(r[1] for r in rows), statistics.mean(r[2] for r in rows)
    print(f"passed {passed}/{len(rows)} · mean paid ${cost:.4f} · mean {secs:.0f}s")
    ok = passed >= 7 and cost <= 0.06 and secs <= 480
    print("GATE " + ("PASS" if ok else "FAIL"))
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
