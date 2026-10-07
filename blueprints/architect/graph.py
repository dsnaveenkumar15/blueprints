# blueprints/architect/graph.py
"""The architect's design graph (spec §3 steps 3-12, lean form) on LangGraph: decide, ground, draft one design, model
capacity, write the decision record, check and fix until the linter and the consistency checks are clean, then one
paid review that also attacks the design. Code nodes compute; model nodes go through the gateway. Every node reports
to `emit`, which powers the live trace. A model step that fails or runs out of budget ends the run "Not ready" with
the reason; it never crashes the run."""
import time
from dataclasses import dataclass, field
from typing import Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from blueprints.architect import alternatives, candidates, consistency, fix, record, review
from blueprints.architect.capacity import capacity
from blueprints.architect.ground import ground
from blueprints.architect.llm import StepFailed
from blueprints.cost import estimate
from blueprints.decision import decide
from blueprints.diagram import to_mermaid
from core.agent.gateway import BudgetExceeded

MAX_FIX_ROUNDS = 4
MAX_REVIEW_ROUNDS = 3
STEP_ERRORS = (BudgetExceeded, RuntimeError, StepFailed)
NODES = ("decide", "ground", "candidates", "capacity", "record", "check", "fix", "review", "finish")


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
    capacity: dict
    problems: list
    check_open: int
    fix_rounds: int
    review: dict | None
    review_rounds: int
    best: dict | None
    best_pass: dict | None
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
        return str(exc) or "the model returned invalid output twice"
    return "the model service failed"


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

    def decide_node(state):
        d = decide(state["brief"]["profile"])
        ctx.emit("info", {"text": f"Topology {d['topology']}; {len(d['mandatory'])} mandatory control(s)"})
        return {"decision": d, "fix_rounds": 0, "review_rounds": 0, "problems": [], "check_open": 0, "note": "", "review": None, "best": None, "best_pass": None,
                "alternatives": alternatives.from_decision(d)}

    def ground_node(state):
        g = ground(state["brief"]["profile"], state["decision"], ctx.index_rows, ctx.entry_for, ctx.patterns,
                   ctx.lint_context, ctx.pricing)
        ctx.emit("info", {"text": f"Grounded in {len(g['references'])} published design(s) and {len(g['patterns'])} pattern(s)"})
        return {"grounding": g}

    def candidates_node(state):
        d, previous = state["decision"], state.get("previous")
        topology = d["topology"]
        if previous and previous.get("topology") in d["allowed_topologies"]:
            topology = previous["topology"]               # a refine keeps the design's shape unless the rules now forbid it
        ctx.emit("candidate", {"topology": topology, "status": "drafting"})
        try:
            body = candidates.draft(gw, topology, state["brief"], d, state["grounding"], previous, state.get("instruction"))
        except STEP_ERRORS as exc:
            note = _reason(exc)
            ctx.emit("candidate", {"topology": topology, "status": "failed", "reason": note})
            return {"note": note, "body": None}
        s = candidates.score(body, ctx.lint_context, ctx.pricing, ctx.clock())
        ctx.emit("candidate", {**s, "status": "scored"})
        return {"body": body, "scores": [s], "chosen_alt": topology != d["topology"]}

    def capacity_node(state):
        cap = capacity(state["body"], ctx.pricing, ctx.clock())
        ctx.emit("capacity", {"latency_s": cap["latency_s"], "target_s": cap["latency_target_s"], "first_limit": cap["first_limit"]["text"]})
        return {"capacity": cap}

    def record_node(state):
        try:
            rec = record.write(gw, state["body"], state["capacity"], state["alternatives"], state.get("assumptions") or [])
        except STEP_ERRORS as exc:          # a missing record is a check problem the fix loop can address
            return {"record": None, "note": _reason(exc)} if isinstance(exc, BudgetExceeded) else {"record": None}
        return {"record": rec}

    def check_node(state):
        body = state["body"]
        cap = capacity(body, ctx.pricing, ctx.clock())
        checks = (consistency.lint_problems(body, ctx.lint_context, bool(state.get("chosen_alt")))
                  + consistency.check(body, state.get("record"), cap, None, ctx.lint_context, ctx.pricing, ctx.clock()))
        ctx.emit("check", {"problems": len(checks), "rules": sorted({c["rule"] for c in checks})[:12],
                           "first": [f"{c['path']}: {c['message'][:110]}" for c in checks[:3]]})
        out = {"capacity": cap, "problems": fix.from_checks(checks), "check_open": len(checks)}
        if not checks:
            out["best"] = {"body": body, "record": state.get("record")}
        return out

    def fix_node(state):
        try:
            body, rec = fix.run(gw, state["body"], state.get("record"), state["problems"], state["brief"], state["body"]["topology"],
                                state["grounding"]["limits"])
        except STEP_ERRORS as exc:
            return {"note": _reason(exc), "fix_rounds": state["fix_rounds"] + 1}
        ctx.emit("info", {"text": f"Fix round {state['fix_rounds'] + 1}: addressed {len(state['problems'])} problem(s)"})
        return {"body": body, "record": rec, "fix_rounds": state["fix_rounds"] + 1}

    def review_node(state):
        try:
            r = review.run(gw, state["body"], state["record"], state["capacity"], state.get("assumptions") or [])
        except STEP_ERRORS as exc:
            return {"note": _reason(exc), "review_rounds": state["review_rounds"] + 1}
        ctx.emit("review", {"scores": r["scores"], "passed": r["passed"], "round": state["review_rounds"] + 1,
                            "attacks": len(r["attacks"])})
        out = {"review": r, "review_rounds": state["review_rounds"] + 1,
               "problems": [] if r["clean"] else review.problems(r), "fix_rounds": 0}
        kept = state.get("best_pass")
        if r["passed"] and (kept is None or r["clean"] or not kept["review"]["clean"]):
            out["best_pass"] = {"body": state["body"], "record": state["record"], "review": r, "round": state["review_rounds"] + 1}
        return out

    def finish_node(state):
        r = state.get("review") or None
        # a pass needs clean linter and consistency checks and a passing review; high attacks left open are residual risks
        passed = bool(state.get("body")) and not state.get("note") and not state.get("check_open") and bool(r and r["passed"])
        best, kept = state.get("best"), state.get("best_pass")
        chosen = best if (not passed and best) else {"body": state.get("body"), "record": state.get("record")}
        earlier = False
        if not passed and kept:                      # a version that already passed review beats a later one that did not
            chosen, r, passed, earlier = kept, kept["review"], True, True
        body = chosen["body"]
        if earlier:
            reason = (f"Passed the architect review on round {kept['round']}; later changes did not improve it, so that "
                      "version is shown.")
        elif passed:
            reason = "Passed the linter, the consistency checks and the architect review (which also attacked the design)."
        elif state.get("note"):
            reason = f"Stopped early: {state['note']}."
        elif r and not r["passed"] and state.get("review_rounds", 0) >= MAX_REVIEW_ROUNDS:
            reason = "The architect review did not pass after three rounds."
        elif state.get("check_open"):
            reason = f"{state['check_open']} check problem(s) remained after the fix rounds."
        else:
            reason = "The architect review did not pass."
        now = ctx.clock()
        result = {"verdict": "passed" if passed else "not_ready", "reason": reason, "brief": state["brief"],
                  "assumptions": state.get("assumptions") or [], "blueprint": body, "record": chosen.get("record"),
                  "variant": None, "variant_cost": None,
                  "capacity": _safe(capacity, body, ctx.pricing, now) if body else None,
                  "cost": _safe(estimate, body, ctx.pricing, now) if body else None,
                  "diagram": _safe(to_mermaid, body, default="") if body else "",
                  "review": r, "redteam": (r or {}).get("attacks") or [], "residual": (r or {}).get("residual") or [], "alternatives": state.get("alternatives") or [],
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
        r = state.get("review")
        if r is None or (not r["clean"] and state["review_rounds"] < MAX_REVIEW_ROUNDS):
            return "review"
        return "finish"

    def after_fix(state):
        return "finish" if state.get("note") else "check"

    def after_review(state):
        if state.get("note"):
            return "finish"
        if state["review"]["clean"] or state["review_rounds"] >= MAX_REVIEW_ROUNDS:
            return "finish"
        return "fix"

    g = StateGraph(State)
    for name, fn in (("decide", decide_node), ("ground", ground_node), ("candidates", candidates_node),
                     ("capacity", capacity_node), ("record", record_node), ("check", check_node), ("fix", fix_node),
                     ("review", review_node), ("finish", finish_node)):
        g.add_node(name, _timed(ctx, name, fn))
    g.add_edge(START, "decide")
    g.add_edge("decide", "ground")
    g.add_edge("ground", "candidates")
    g.add_conditional_edges("candidates", after_candidates, ["capacity", "finish"])
    g.add_edge("capacity", "record")
    g.add_conditional_edges("record", after_record, ["check", "finish"])
    g.add_conditional_edges("check", after_check, ["fix", "review", "finish"])
    g.add_conditional_edges("fix", after_fix, ["check", "finish"])
    g.add_conditional_edges("review", after_review, ["fix", "finish"])
    g.add_edge("finish", END)
    return g.compile()


def run_design(ctx: Context, inputs: dict) -> dict:
    return build_graph(ctx).invoke(dict(inputs), {"recursion_limit": 150})["result"]
