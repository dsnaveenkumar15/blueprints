# blueprints/architect/graph.py
"""The architect's design graph (spec §3 steps 3-12) on LangGraph. Code nodes compute; model nodes go through the
gateway. Every node reports to `emit`, which powers the live trace. A model step that fails or runs out of budget ends
the run "Not ready" with the reason; it never crashes the run."""
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from blueprints.architect import candidates, consistency, fix, record, redteam, review
from blueprints.architect.capacity import capacity
from blueprints.architect.ground import ground
from blueprints.architect.llm import StepFailed
from blueprints.cost import estimate
from blueprints.decision import decide
from blueprints.diagram import to_mermaid
from core.agent.gateway import BudgetExceeded

MAX_FIX_ROUNDS = 4
MAX_REVIEW_ROUNDS = 2
STEP_ERRORS = (BudgetExceeded, RuntimeError, StepFailed)
NODES = ("decide", "ground", "candidates", "capacity", "record", "check", "fix", "redteam", "review", "finish")


class State(TypedDict, total=False):
    brief: dict
    assumptions: list
    previous: dict | None
    instruction: str | None
    decision: dict
    grounding: dict
    scores: list
    alternatives: list
    chosen_alt: bool
    body: dict
    record: dict | None
    variant: dict | None
    capacity: dict
    problems: list
    fix_rounds: int
    redteam: list
    redteam_done: bool
    review: dict | None
    review_rounds: int
    best: dict | None
    note: str
    result: dict


@dataclass
class Context:
    gateway: object
    emit: Callable[[str, dict], None]
    lint_context: dict
    pricing: dict
    index_rows: list = field(default_factory=list)
    entry_for: Callable = lambda _id: None
    patterns: list = field(default_factory=list)
    clock: Callable[[], float] = time.time


def _reason(exc: Exception) -> str:
    if isinstance(exc, BudgetExceeded):
        return str(exc)
    if isinstance(exc, StepFailed):
        return "the model returned invalid output twice"
    return "the model service failed"


def _locked(lock, emit, type, payload):
    with lock:
        emit(type, payload)


def _timed(ctx: Context, name: str, fn):
    def node(state: State) -> dict:
        ctx.emit("node", {"name": name, "status": "start"})
        t = time.monotonic()
        out = fn(state)
        ctx.emit("node", {"name": name, "status": "end", "ms": int((time.monotonic() - t) * 1000)})
        return out
    return node


def _safe(fn, *args, default=None):
    try:
        return fn(*args)
    except Exception:                      # a raw body one engine cannot read must not hide the rest of the result
        return default


def build_graph(ctx: Context):
    gw = ctx.gateway
    lock = threading.Lock()
    emit = ctx.emit
    ctx.emit = lambda type, payload: _locked(lock, emit, type, payload)         # worker threads report too: one writer at a time

    def decide_node(state):
        d = decide(state["brief"]["profile"])
        ctx.emit("info", {"text": f"Topology {d['topology']}; {len(d['mandatory'])} mandatory control(s)"})
        return {"decision": d, "fix_rounds": 0, "review_rounds": 0, "redteam_done": False, "problems": [], "note": "",
                "review": None, "best": None, "redteam": []}

    def ground_node(state):
        g = ground(state["brief"]["profile"], state["decision"], ctx.index_rows, ctx.entry_for, ctx.patterns,
                   ctx.lint_context, ctx.pricing)
        ctx.emit("info", {"text": f"Grounded in {len(g['references'])} published design(s) and {len(g['patterns'])} pattern(s)"})
        return {"grounding": g}

    def candidates_node(state):
        d, previous = state["decision"], state.get("previous")
        if previous:
            tops = [previous.get("topology") if previous.get("topology") in d["allowed_topologies"] else d["topology"]]
        else:
            tops = candidates.topologies(d)
        bodies, scored, note, out_of_budget = [], [], "", False
        for t in tops:
            ctx.emit("candidate", {"topology": t, "status": "drafting"})
        with ThreadPoolExecutor(max_workers=len(tops)) as pool:                 # both drafts in flight together
            futures = [(t, pool.submit(candidates.draft, gw, t, state["brief"], d, state["grounding"], previous,
                                       state.get("instruction"))) for t in tops]
        for t, future in futures:
            try:
                b = future.result()
            except STEP_ERRORS as exc:
                note = _reason(exc)
                ctx.emit("candidate", {"topology": t, "status": "failed", "reason": note})
                out_of_budget = out_of_budget or isinstance(exc, BudgetExceeded)
                continue
            s = candidates.score(b, d, ctx.lint_context, ctx.pricing, ctx.clock())
            bodies.append(b)
            scored.append(s)
            ctx.emit("candidate", {**s, "status": "scored"})
        if not bodies:
            return {"note": note or "no candidate design could be drafted", "body": None}
        i = candidates.choose(scored)
        alts = [{"topology": scored[j]["topology"], "summary": f"The {scored[j]['topology']} topology",
                 "rejected_because": "; ".join(candidates.penalties(scored[i], scored[j]))}
                for j in range(len(scored)) if j != i]
        ctx.emit("info", {"text": f"Chose the {scored[i]['topology']} design"})
        return {"body": bodies[i], "scores": scored, "alternatives": alts, "chosen_alt": not scored[i]["primary"],
                "note": note if out_of_budget else ""}        # out of budget: finish with the one design drafted

    def capacity_node(state):
        cap = capacity(state["body"], ctx.pricing, ctx.clock())
        ctx.emit("capacity", {"latency_s": cap["latency_s"], "target_s": cap["latency_target_s"], "first_limit": cap["first_limit"]["text"]})
        return {"capacity": cap}

    def record_node(state):
        """The record and the cheaper variant only need the chosen design, so they are written at the same time."""
        out = {"record": None, "variant": None}
        with ThreadPoolExecutor(max_workers=2) as pool:
            rec = pool.submit(record.write, gw, state["body"], state["capacity"], state["alternatives"], state.get("assumptions") or [])
            lean = pool.submit(record.variant, gw, state["body"], state["grounding"], state["brief"])
        for key, future in (("record", rec), ("variant", lean)):
            try:
                out[key] = future.result()
            except STEP_ERRORS as exc:        # a missing record or variant is a check problem the fix loop can address
                if isinstance(exc, BudgetExceeded):
                    out["note"] = _reason(exc)
        return out

    def check_node(state):
        body = state["body"]
        cap = capacity(body, ctx.pricing, ctx.clock())
        lean = (state.get("variant") or {}).get("blueprint")
        checks = (consistency.lint_problems(body, ctx.lint_context, bool(state.get("chosen_alt")))
                  + consistency.check(body, state.get("record"), cap, lean, ctx.lint_context, ctx.pricing, ctx.clock()))
        ctx.emit("check", {"problems": len(checks), "rules": sorted({c["rule"] for c in checks})[:12]})
        out = {"capacity": cap, "problems": fix.from_checks(checks)}
        if not checks:
            out["best"] = {"body": body, "record": state.get("record"), "variant": state.get("variant")}
        return out

    def fix_node(state):
        v = state.get("variant") or {}
        try:
            body, rec, lean = fix.run(gw, state["body"], state.get("record"), v.get("blueprint"), state["problems"],
                                      state["brief"], state["body"]["topology"])
            variant = {**v, "blueprint": lean} if lean else None
            if variant is None:
                variant = record.variant(gw, body, state["grounding"], state["brief"])
        except STEP_ERRORS as exc:
            return {"note": _reason(exc), "fix_rounds": state["fix_rounds"] + 1}
        ctx.emit("info", {"text": f"Fix round {state['fix_rounds'] + 1}: addressed {len(state['problems'])} problem(s)"})
        return {"body": body, "record": rec, "variant": variant, "fix_rounds": state["fix_rounds"] + 1}

    def redteam_node(state):
        try:
            findings = redteam.run(gw, state["body"])
        except STEP_ERRORS as exc:
            if isinstance(exc, BudgetExceeded):
                return {"note": _reason(exc)}
            findings = []
        probs = redteam.problems(findings)
        ctx.emit("redteam", {"findings": len(findings), "significant": len(probs)})
        return {"redteam": findings, "redteam_done": True, "problems": probs, "fix_rounds": 0}

    def review_node(state):
        try:
            r = review.run(gw, state["body"], state["record"], state["capacity"], state.get("redteam") or [],
                           state.get("assumptions") or [])
        except STEP_ERRORS as exc:
            return {"note": _reason(exc), "review_rounds": state["review_rounds"] + 1}
        ctx.emit("review", {"scores": r["scores"], "passed": r["passed"], "round": state["review_rounds"] + 1})
        return {"review": r, "review_rounds": state["review_rounds"] + 1,
                "problems": [] if r["passed"] else review.problems(r), "fix_rounds": 0}

    def finish_node(state):
        r = state.get("review") or None
        clean = bool(state.get("body")) and not state.get("problems") and not state.get("note")
        passed = clean and bool(r and r["passed"])
        best = state.get("best")
        chosen = best if (not passed and best) else {"body": state.get("body"), "record": state.get("record"),
                                                     "variant": state.get("variant")}
        body, lean = chosen["body"], (chosen.get("variant") or {}).get("blueprint")
        if passed:
            reason = "Passed the linter, the consistency checks, the red team and the architect review."
        elif state.get("note"):
            reason = f"Stopped early: {state['note']}."
        elif r and not r["passed"] and state.get("review_rounds", 0) >= MAX_REVIEW_ROUNDS:
            reason = "The architect review did not pass after two rounds."
        elif state.get("problems"):
            reason = f"{len(state['problems'])} problem(s) remained after the fix rounds."
        else:
            reason = "The architect review did not pass."
        now = ctx.clock()
        result = {"verdict": "passed" if passed else "not_ready", "reason": reason, "brief": state["brief"],
                  "assumptions": state.get("assumptions") or [], "blueprint": body, "record": chosen.get("record"),
                  "variant": chosen.get("variant"),
                  "variant_cost": _safe(estimate, lean, ctx.pricing, now) if lean else None,
                  "capacity": _safe(capacity, body, ctx.pricing, now) if body else None,
                  "cost": _safe(estimate, body, ctx.pricing, now) if body else None,
                  "diagram": _safe(to_mermaid, body, default="") if body else "",
                  "review": r, "redteam": state.get("redteam") or [], "alternatives": state.get("alternatives") or [],
                  "scores": state.get("scores") or [], "problems": state.get("problems") or [], "decision": state.get("decision")}
        ctx.emit("result", {"verdict": result["verdict"], "reason": reason})
        return {"result": result}

    def after_candidates(state):
        return "capacity" if state.get("body") and not state.get("note") else "finish"

    def after_record(state):
        return "finish" if state.get("note") else "check"

    def after_check(state):
        if state.get("note"):
            return "finish"
        if state["problems"]:
            return "fix" if state["fix_rounds"] < MAX_FIX_ROUNDS else "finish"
        if not state.get("redteam_done"):
            return "redteam"
        r = state.get("review")
        if r is None or (not r["passed"] and state["review_rounds"] < MAX_REVIEW_ROUNDS):
            return "review"
        return "finish"

    def after_fix(state):
        return "finish" if state.get("note") else "check"

    def after_redteam(state):
        if state.get("note"):
            return "finish"
        return "fix" if state["problems"] else "review"

    def after_review(state):
        if state.get("note"):
            return "finish"
        if state["review"]["passed"] or state["review_rounds"] >= MAX_REVIEW_ROUNDS:
            return "finish"
        return "fix"

    g = StateGraph(State)
    for name, fn in (("decide", decide_node), ("ground", ground_node), ("candidates", candidates_node),
                     ("capacity", capacity_node), ("record", record_node), ("check", check_node), ("fix", fix_node),
                     ("redteam", redteam_node), ("review", review_node), ("finish", finish_node)):
        g.add_node(name, _timed(ctx, name, fn))
    g.add_edge(START, "decide")
    g.add_edge("decide", "ground")
    g.add_edge("ground", "candidates")
    g.add_conditional_edges("candidates", after_candidates, ["capacity", "finish"])
    g.add_edge("capacity", "record")
    g.add_conditional_edges("record", after_record, ["check", "finish"])
    g.add_conditional_edges("check", after_check, ["fix", "redteam", "review", "finish"])
    g.add_conditional_edges("fix", after_fix, ["check", "finish"])
    g.add_conditional_edges("redteam", after_redteam, ["fix", "review", "finish"])
    g.add_conditional_edges("review", after_review, ["fix", "finish"])
    g.add_edge("finish", END)
    return g.compile()


def run_design(ctx: Context, inputs: dict) -> dict:
    return build_graph(ctx).invoke(dict(inputs), {"recursion_limit": 150})["result"]
