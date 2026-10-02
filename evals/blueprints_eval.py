"""Golden decision profiles plus known-good and known-bad blueprints. CI fails if the linter's precision or recall drops."""
import copy
import sys

from blueprints.cost import load_pricing
from blueprints.decision import decide
from blueprints.linter import lint
from blueprints.store import load_seeds

MIN_PRECISION = 0.9
MIN_RECALL = 0.9
BASE = {"latency": "batch", "volume_per_day": 100, "risk": "low", "data_sensitivity": "public", "autonomy": "suggest",
        "budget_usd_month": 100, "capabilities": ["read"], "open_ended": False, "task_kinds": 1, "parallelizable": False,
        "knowledge": False, "quality_critical": False}


def p(**over):
    return {**BASE, **over}


GOLDEN = [
    ("plain batch job", p(), "workflow", []),
    ("several request kinds", p(task_kinds=3, latency="interactive"), "router", []),
    ("quality critical", p(quality_critical=True), "evaluator-optimizer", []),
    ("open-ended with fan-out", p(open_ended=True, parallelizable=True, risk="medium", autonomy="act_with_approval"),
     "orchestrator-workers", ["rate_limit"]),
    ("autonomous coder", p(open_ended=True, risk="medium", autonomy="act_autonomously", capabilities=["read", "write", "exec"]),
     "autonomous", ["human_gate", "rate_limit", "sandbox"]),
    ("interactive blocks agents", p(latency="interactive", open_ended=True, autonomy="act_autonomously", risk="medium"), "workflow", []),
    ("high risk blocks autonomy", p(open_ended=True, risk="high", autonomy="act_autonomously"), "supervisor", ["rate_limit"]),
    ("regulated data", p(data_sensitivity="regulated"), "workflow", ["audit_log", "input_filter", "output_filter"]),
    ("money movement", p(risk="high", capabilities=["read", "payment"], autonomy="act_with_approval"), "workflow", ["human_gate"]),
]


def bad_cases():
    """(name, seed id, mutation, expected rule). Each mutation breaks one thing in an otherwise clean seed."""
    def remove_mitigation(bp):
        for c in bp["components"]:
            c["mitigations"] = []

    def mitigate_with_model(bp):
        for c in bp["components"]:
            for m in c["mitigations"]:
                m["by"] = next(x["id"] for x in bp["components"] if x["kind"] == "model")

    def ghost_failure_component(bp):
        bp["failure_modes"][0]["component"] = "ghost"

    def ghost_threat_mitigation(bp):
        bp["threats"][0]["mitigation"] = "ghost"

    def bad_owasp(bp):
        bp["threats"][0]["owasp"] = "LLM99"

    def duplicate_id(bp):
        bp["guardrails"][0]["id"] = bp["components"][0]["id"]

    def drop_guardrails(bp):
        bp["guardrails"] = []

    def forbidden_topology(bp):
        bp["topology"] = "autonomous"

    def unknown_model(bp):
        next(c for c in bp["components"] if c["kind"] == "model")["model"] = "gpt-imaginary"

    def unknown_pattern(bp):
        bp["patterns"] = ["magic"]

    def extra_field(bp):
        bp["surprise"] = True

    def step_on_tool(bp):
        bp["flow"][0]["component"] = next(c["id"] for c in bp["components"] if c["kind"] != "model")

    def drop_retrieval(bp):
        removed = {c["id"] for c in bp["components"] if c["kind"] == "retrieval"}
        bp["components"] = [c for c in bp["components"] if c["kind"] != "retrieval"]
        fallback = bp["components"][0]["id"]
        for f in bp["failure_modes"]:
            if f["component"] in removed:
                f["component"] = fallback
        for th in bp["threats"]:
            if th["component"] in removed:
                th["component"] = fallback

    def unwired_gate(bp):
        audit = next(g["id"] for g in bp["guardrails"] if g["kind"] == "audit_log")
        for c in bp["components"]:
            for m in c["mitigations"]:
                m["by"] = audit

    def undeclared_capability(bp):
        for c in bp["components"]:
            c["capabilities"] = [cap for cap in c["capabilities"] if cap not in ("write", "exec", "payment", "network")]
            c["mitigations"] = []

    def idle_filter(bp):
        for g in bp["guardrails"]:
            if g["kind"] == "input_filter":
                g["protects"] = []

    def graded_tool(bp):
        next(c for c in bp["components"] if c["kind"] == "tool")["tool_ref"] = "acme/orders-mcp"

    def untracked_framework(bp):
        next(c for c in bp["components"] if c["kind"] == "model")["framework_ref"] = "pypi:made-up-framework"

    def autonomous_write(bp):
        audit = next(g["id"] for g in bp["guardrails"] if g["kind"] == "audit_log")
        gates = {c["id"] for c in bp["components"] if c["kind"] == "human_gate"}
        bp["components"] = [c for c in bp["components"] if c["kind"] != "human_gate"]
        for c in bp["components"]:
            for m in c["mitigations"]:
                m["by"] = audit if m["by"] in gates else m["by"]
        for f in bp["failure_modes"] + bp["threats"]:
            if f["mitigation"] in gates:
                f["mitigation"] = audit
        bp["guardrails"] = [g for g in bp["guardrails"] if g["kind"] != "approval_gate"]
        bp["profile"].update(risk="medium", latency="batch", autonomy="act_autonomously", open_ended=True, task_kinds=1, knowledge=False)
        bp["topology"] = "autonomous"

    return [
        ("tool graded below B", "support-agent", graded_tool, "tool-not-eligible", {"trust_grades": {"acme/orders-mcp": "C"}}),
        ("tool with no Trust grade", "support-agent", graded_tool, "tool-not-eligible", {"trust_grades": {}}),
        ("framework Radar does not track", "docs-rag", untracked_framework, "unknown-framework", {"frameworks": set()}),
        ("autonomous write without an approval gate", "support-agent", autonomous_write, "autonomous-write-without-gate", {}),
        ("write without any control", "support-agent", remove_mitigation, "unmitigated-capability"),
        ("mitigated by a model", "support-agent", mitigate_with_model, "mitigation-not-a-control"),
        ("failure mode on a ghost", "dispute-handling", ghost_failure_component, "dangling-ref"),
        ("threat mitigated by a ghost", "code-review-agent", ghost_threat_mitigation, "dangling-ref"),
        ("invented OWASP id", "docs-rag", bad_owasp, "unknown-owasp"),
        ("duplicate id", "dispute-handling", duplicate_id, "duplicate-id"),
        ("no guardrails at all", "dispute-handling", drop_guardrails, "missing-mandatory"),
        ("forbidden topology", "support-agent", forbidden_topology, "forbidden-topology"),
        ("unpriced model", "docs-rag", unknown_model, "unknown-model"),
        ("unknown pattern", "code-review-agent", unknown_pattern, "unknown-pattern"),
        ("unknown field", "docs-rag", extra_field, "schema"),
        ("flow step on a non-model", "dispute-handling", step_on_tool, "step-not-a-model"),
        ("knowledge task without retrieval", "docs-rag", drop_retrieval, "missing-mandatory"),
        ("gate exists but guards nothing", "support-agent", unwired_gate, "gate-not-wired"),
        ("acting capability declared by nobody", "support-agent", undeclared_capability, "capability-undeclared"),
        ("required input filter protects nothing", "dispute-handling", idle_filter, "mandatory-guardrail-idle"),
    ]


BAD_CASES = bad_cases()


def evaluate() -> dict:
    context = {"models": set(load_pricing()["models"])}
    seeds = {s["id"]: s for s in load_seeds()}
    clean = sum(1 for s in seeds.values() if lint(s, context)["violations"] == [])
    bad = {}
    for name, seed_id, mutate, rule, *extra in BAD_CASES:
        bp = copy.deepcopy(seeds[seed_id])
        mutate(bp)
        result = lint(bp, {**context, **(extra[0] if extra else {})})
        bad[name] = (not result["ok"]) and any(v["rule"] == rule and v["severity"] == "error" for v in result["violations"])
    golden = {}
    for name, profile, topology, kinds in GOLDEN:
        d = decide(profile)
        golden[name] = d["topology"] == topology and sorted(m["kind"] for m in d["mandatory"]) == sorted(kinds)
    return {"precision": clean / len(seeds), "recall": sum(bad.values()) / len(bad),
            "decision_accuracy": sum(golden.values()) / len(golden), "bad": bad, "golden": golden}


def main() -> int:
    result = evaluate()
    for name, ok in {**result["bad"], **{f"decision: {k}": v for k, v in result["golden"].items()}}.items():
        print(f"{'ok  ' if ok else 'FAIL'} {name}")
    print(f"precision={result['precision']:.2f} (min {MIN_PRECISION})  recall={result['recall']:.2f} (min {MIN_RECALL})  "
          f"decisions={result['decision_accuracy']:.2f}")
    good = result["precision"] >= MIN_PRECISION and result["recall"] >= MIN_RECALL and result["decision_accuracy"] == 1.0
    return 0 if good else 1


if __name__ == "__main__":
    sys.exit(main())
