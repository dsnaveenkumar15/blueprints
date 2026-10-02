"""Requirements profile -> topology, mandatory controls and patterns. Deterministic code decides; every step is traced."""
from blueprints.models import HIGH_RISK_CAPABILITIES, TOPOLOGIES, Profile

RATE_LIMIT_VOLUME = 10_000
ACTING = {"write", "exec", "payment"}

RULES = {
    "interactive-latency": "Interactive latency leaves no room for loops: only a workflow or a router is allowed.",
    "high-risk-no-autonomy": "High risk never allows a fully autonomous topology.",
    "prefer-workflow": "Without an open-ended task, a plain workflow beats any agentic topology.",
    "autonomous-needs-consent": "A fully autonomous topology needs autonomy to be act_autonomously.",
    "open-ended-autonomous": "An open-ended task with autonomy granted may run as an autonomous agent.",
    "open-ended-fanout": "An open-ended task whose subtasks can run in parallel uses orchestrator and workers.",
    "open-ended-lead": "An open-ended task is led by a supervisor when nothing stricter applies.",
    "open-ended-fallback": "Open-ended, but the allowed topologies exclude agents: falling back to the plain shape.",
    "multiple-task-kinds": "Several distinct request kinds are best separated by a router.",
    "quality-critical": "A quality-critical task with criteria uses an evaluator and optimizer loop.",
    "default-workflow": "A single kind of task with fixed steps is a workflow.",
    "hitl-high-risk-action": "High risk with an acting capability (write, exec, payment) needs a human gate.",
    "hitl-approval-mode": "Autonomy set to act_with_approval and an acting or networked capability needs a human gate.",
    "autonomous-write-gate": "An autonomous agent with write access needs an approval gate.",
    "pii-protection": "Personal or regulated data needs input filtering and an audit log.",
    "regulated-output": "Regulated data also needs output filtering.",
    "isolate-network-exec": "Network or code execution needs a sandbox.",
    "knowledge-retrieval": "A knowledge-heavy task needs a retrieval component.",
    "bound-consumption": "High volume or a looping topology needs a rate limit (unbounded consumption).",
}


def decide(profile: dict | Profile) -> dict:
    p = profile if isinstance(profile, Profile) else Profile.model_validate(profile)
    caps = set(p.capabilities)
    trace: list[dict] = []
    allowed = list(TOPOLOGIES)

    def note(rule: str, effect: str, because: str) -> None:
        trace.append({"rule": rule, "effect": effect, "because": because})

    def drop(*names: str) -> None:
        for name in names:
            if name in allowed:
                allowed.remove(name)

    if p.latency == "interactive":
        drop("orchestrator-workers", "supervisor", "evaluator-optimizer", "autonomous")
        note("interactive-latency", "only workflow or router", "interactive latency")
    if p.risk == "high":
        drop("autonomous")
        note("high-risk-no-autonomy", "autonomous topology removed", "risk is high")
    if not p.open_ended:
        drop("autonomous", "supervisor")
        note("prefer-workflow", "agentic topologies removed", "the task is not open-ended")
    if p.autonomy != "act_autonomously":
        drop("autonomous")
        note("autonomous-needs-consent", "autonomous topology removed", f"autonomy is {p.autonomy}")

    if p.open_ended:
        if p.autonomy == "act_autonomously" and "autonomous" in allowed:
            topology = "autonomous"
            note("open-ended-autonomous", "autonomous", "open-ended task with autonomy granted")
        elif p.parallelizable and "orchestrator-workers" in allowed:
            topology = "orchestrator-workers"
            note("open-ended-fanout", "orchestrator-workers", "open-ended task with parallel subtasks")
        elif "supervisor" in allowed:
            topology = "supervisor"
            note("open-ended-lead", "supervisor", "open-ended task")
        else:
            topology = "router" if p.task_kinds > 1 else "workflow"
            note("open-ended-fallback", topology, "the allowed topologies exclude agents")
    elif p.task_kinds > 1:
        topology = "router"
        note("multiple-task-kinds", "router", f"{p.task_kinds} request kinds")
    elif p.quality_critical and "evaluator-optimizer" in allowed:
        topology = "evaluator-optimizer"
        note("quality-critical", "evaluator-optimizer", "quality-critical task")
    else:
        topology = "workflow"
        note("default-workflow", "workflow", "one kind of task with fixed steps")

    mandatory: list[dict] = []

    def need(kind: str, rule: str) -> None:
        if all(m["kind"] != kind for m in mandatory):
            mandatory.append({"kind": kind, "rule": rule})

    if p.risk == "high" and caps & ACTING:
        need("human_gate", "hitl-high-risk-action")
    if p.autonomy == "act_with_approval" and caps & set(HIGH_RISK_CAPABILITIES):
        need("human_gate", "hitl-approval-mode")
    if topology == "autonomous" and "write" in caps:
        need("human_gate", "autonomous-write-gate")
    if p.data_sensitivity in ("pii", "regulated"):
        need("input_filter", "pii-protection")
        need("audit_log", "pii-protection")
    if p.data_sensitivity == "regulated":
        need("output_filter", "regulated-output")
    if caps & {"network", "exec"}:
        need("sandbox", "isolate-network-exec")
    if p.knowledge:
        need("retrieval", "knowledge-retrieval")
    if p.volume_per_day >= RATE_LIMIT_VOLUME or topology in ("autonomous", "supervisor", "orchestrator-workers"):
        need("rate_limit", "bound-consumption")

    patterns = {"workflow": ["prompt-chaining"], "router": ["routing"], "orchestrator-workers": ["orchestrator-workers"],
                "supervisor": ["handoff"], "evaluator-optimizer": ["evaluator-optimizer"], "autonomous": ["react"]}[topology][:]
    if p.parallelizable and "parallelization" not in patterns:
        patterns.append("parallelization")
    if p.knowledge:
        patterns.append("rag")
    if any(m["kind"] == "human_gate" for m in mandatory):
        patterns.append("hitl-approval")
    return {"topology": topology, "allowed_topologies": allowed, "mandatory": mandatory, "patterns": patterns, "trace": trace}
