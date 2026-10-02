"""The one blueprint linter (spec §7.4). The M3b endpoint /api/lint-blueprint calls this same function.
Errors block approval and publishing; warnings are shown."""
import re

from pydantic import ValidationError

from blueprints.decision import decide
from blueprints.models import HIGH_RISK_CAPABILITIES, Blueprint
from blueprints.owasp import OWASP_LLM
from blueprints.patterns import pattern_ids

ERROR, WARNING = "error", "warning"
ACTING = {"write", "exec", "payment"}
MAX_SCHEMA_VIOLATIONS = 50
VOLATILE = re.compile(r"\$\s?\d|\b\d+\.\d+\.\d+\b|\b(?:19|20)\d{2}\b|\bgrade\s+[A-F]\b", re.I)

RULES = {
    "schema": "The blueprint must match the schema exactly: no unknown fields, valid enums and ids, bounded sizes.",
    "duplicate-id": "Component and guardrail ids are unique.",
    "dangling-ref": "Every reference (protects, flow component, failure-mode and threat component, mitigation) points at an existing id.",
    "step-not-a-model": "A flow step spends tokens, so its component must be a model.",
    "unmitigated-capability": "Every high-risk capability (write, exec, network, payment) on a component has a mitigation.",
    "mitigation-not-a-control": "A mitigation must be a guardrail or a human gate, not a model or a tool.",
    "unknown-owasp": "Threat ids must exist in the OWASP Top 10 for LLM Applications (2025).",
    "unknown-pattern": "Patterns must exist in the pattern catalog.",
    "missing-mandatory": "Controls the decision engine requires for this requirements profile are present.",
    "forbidden-topology": "The topology must be one the decision engine allows for this profile.",
    "autonomous-write-without-gate": "An autonomous agent with write access and no approval gate is a violation.",
    "model-missing": "A model component names its model.",
    "unknown-model": "A model must be in the priced model table, so cost is never invented.",
    "tool-not-eligible": "A tool may be referenced only if its Trust grade is A or B.",
    "unknown-framework": "A framework reference must be a package Radar tracks.",
    "topology-differs": "The topology is allowed but differs from the decision engine's choice.",
    "guardrail-protects-nothing": "A guardrail should protect at least one component.",
    "excessive-agency-unmapped": "A high-risk blueprint with an acting capability should map OWASP LLM06 (Excessive Agency).",
    "unused-model": "A model component that no flow step uses is dead weight.",
    "capability-undeclared": "Every high-risk capability in the requirements profile is declared by some component, so it cannot dodge its controls.",
    "gate-not-wired": "When the decision engine requires a human gate, each acting capability is mitigated by a human gate or an approval gate.",
    "tool-without-reference": "A tool component with no tool_ref cannot be graded by Trust, so nothing vouches for it.",
    "volatile-fact-in-text": "Free text should not state prices, versions, years or grades: they go stale and belong in the dated tables.",
    "mandatory-guardrail-idle": "A guardrail that the decision engine requires must protect at least one component.",
}


def _v(rule: str, severity: str, path: str, message: str) -> dict:
    return {"rule": rule, "severity": severity, "path": path, "message": message}


def _result(violations: list[dict]) -> dict:
    errors = sum(1 for v in violations if v["severity"] == ERROR)
    return {"ok": errors == 0, "errors": errors, "warnings": len(violations) - errors, "violations": violations}


def lint(blueprint, context: dict | None = None) -> dict:
    context = context or {}
    try:
        bp = Blueprint.model_validate(blueprint)
    except ValidationError as exc:
        return _result([_v("schema", ERROR, ".".join(str(p) for p in e["loc"]) or "(root)", e["msg"])
                        for e in exc.errors()[:MAX_SCHEMA_VIOLATIONS]])
    except Exception as exc:                       # RecursionError or anything else odd in hostile input
        return _result([_v("schema", ERROR, "(root)", f"not a valid blueprint ({type(exc).__name__})")])

    out: list[dict] = []
    comps = {c.id: c for c in bp.components}
    guards = {g.id: g for g in bp.guardrails}
    all_ids = [c.id for c in bp.components] + [g.id for g in bp.guardrails]
    for dup in sorted({i for i in all_ids if all_ids.count(i) > 1}):
        out.append(_v("duplicate-id", ERROR, dup, f"id {dup!r} is used more than once"))
    known = set(comps) | set(guards)
    controls = {i for i, c in comps.items() if c.kind == "human_gate"} | set(guards)

    for g in bp.guardrails:
        if not g.protects:
            out.append(_v("guardrail-protects-nothing", WARNING, f"guardrails.{g.id}", "protects no component"))
        for p in g.protects:
            if p not in comps:
                out.append(_v("dangling-ref", ERROR, f"guardrails.{g.id}.protects", f"{p!r} is not a component"))
    for s in bp.flow:
        if s.component not in comps:
            out.append(_v("dangling-ref", ERROR, f"flow.{s.id}", f"{s.component!r} is not a component"))
        elif comps[s.component].kind != "model":
            out.append(_v("step-not-a-model", ERROR, f"flow.{s.id}", f"{s.component!r} is a {comps[s.component].kind}, not a model"))
    for f in bp.failure_modes:
        if f.component not in comps:
            out.append(_v("dangling-ref", ERROR, f"failure_modes.{f.id}.component", f"{f.component!r} is not a component"))
        if f.mitigation not in known:
            out.append(_v("dangling-ref", ERROR, f"failure_modes.{f.id}.mitigation", f"{f.mitigation!r} does not exist"))
    for i, t in enumerate(bp.threats):
        if t.owasp not in OWASP_LLM:
            out.append(_v("unknown-owasp", ERROR, f"threats.{i}.owasp", f"{t.owasp} is not in the OWASP LLM Top 10 (2025)"))
        if t.component not in comps:
            out.append(_v("dangling-ref", ERROR, f"threats.{i}.component", f"{t.component!r} is not a component"))
        if t.mitigation not in known:
            out.append(_v("dangling-ref", ERROR, f"threats.{i}.mitigation", f"{t.mitigation!r} does not exist"))

    for c in bp.components:
        for m in c.mitigations:
            if m.by not in known:
                out.append(_v("dangling-ref", ERROR, f"components.{c.id}.mitigations", f"{m.by!r} does not exist"))
            elif m.by not in controls:
                out.append(_v("mitigation-not-a-control", ERROR, f"components.{c.id}.mitigations",
                              f"{m.by!r} is not a guardrail or a human gate"))
        for cap in c.capabilities:
            if cap in HIGH_RISK_CAPABILITIES and not any(m.capability == cap and m.by in controls for m in c.mitigations):
                out.append(_v("unmitigated-capability", ERROR, f"components.{c.id}", f"capability {cap!r} has no control"))
        if c.kind == "model":
            if not c.model:
                out.append(_v("model-missing", ERROR, f"components.{c.id}", "a model component must name its model"))
            elif context.get("models") is not None and c.model not in context["models"]:
                out.append(_v("unknown-model", ERROR, f"components.{c.id}.model", f"{c.model!r} is not in the priced model table"))
            if c.id not in {s.component for s in bp.flow}:
                out.append(_v("unused-model", WARNING, f"components.{c.id}", "no flow step uses this model"))
        grades = context.get("trust_grades")
        if grades is not None and c.tool_ref and grades.get(c.tool_ref.lower()) not in ("A", "B"):
            out.append(_v("tool-not-eligible", ERROR, f"components.{c.id}.tool_ref",
                          f"{c.tool_ref} has Trust grade {grades.get(c.tool_ref.lower()) or 'none'}; only A or B tools are recommended"))
        if grades is not None and c.kind == "tool" and not c.tool_ref:
            out.append(_v("tool-without-reference", WARNING, f"components.{c.id}", "a tool with no tool_ref cannot be graded by Trust"))
        tracked = context.get("frameworks")
        if tracked is not None and c.framework_ref and c.framework_ref not in tracked:
            out.append(_v("unknown-framework", ERROR, f"components.{c.id}.framework_ref", f"{c.framework_ref} is not tracked by Radar"))

    texts = [("summary", bp.summary), ("deployment.runtime", bp.deployment.runtime), ("deployment.notes", bp.deployment.notes)]
    texts += [(f"components.{c.id}.role", c.role) for c in bp.components] + [(f"guardrails.{g.id}", g.description) for g in bp.guardrails]
    texts += [(f"failure_modes.{f.id}", f.description) for f in bp.failure_modes] + [(f"threats.{i}.note", t.note) for i, t in enumerate(bp.threats)]
    texts += [(f"observability.{i}", o) for i, o in enumerate(bp.observability)]
    for path, text in texts:
        found = VOLATILE.search(text or "")
        if found:
            out.append(_v("volatile-fact-in-text", WARNING, path, f"states {found.group(0)!r}; prices, versions, years and grades belong in the dated tables"))

    catalog = pattern_ids()
    for name in bp.patterns:
        if name not in catalog:
            out.append(_v("unknown-pattern", ERROR, "patterns", f"{name!r} is not in the pattern catalog"))

    acting = any(set(c.capabilities) & ACTING for c in bp.components)
    has_gate = any(c.kind == "human_gate" for c in bp.components) or any(g.kind == "approval_gate" for g in bp.guardrails)
    if bp.topology == "autonomous" and acting and not has_gate:
        out.append(_v("autonomous-write-without-gate", ERROR, "topology", "an autonomous agent can act but has no approval gate"))

    decision = decide(bp.profile.model_dump())
    declared = {cap for c in bp.components for cap in c.capabilities}
    for cap in bp.profile.capabilities:
        if cap in HIGH_RISK_CAPABILITIES and cap not in declared:
            out.append(_v("capability-undeclared", ERROR, "components", f"the profile needs {cap!r} but no component declares it"))
    gates = {c.id for c in bp.components if c.kind == "human_gate"} | {g.id for g in bp.guardrails if g.kind == "approval_gate"}
    if any(m["kind"] == "human_gate" for m in decision["mandatory"]):
        for c in bp.components:
            for cap in sorted(set(c.capabilities) & ACTING):
                if not any(m.capability == cap and m.by in gates for m in c.mitigations):
                    out.append(_v("gate-not-wired", ERROR, f"components.{c.id}",
                                  f"capability {cap!r} must be mitigated by a human gate or an approval gate"))
    present = {c.kind for c in bp.components} | {g.kind for g in bp.guardrails} | ({"human_gate"} if has_gate else set())
    for need in decision["mandatory"]:
        if need["kind"] not in present:
            out.append(_v("missing-mandatory", ERROR, "components", f"{need['kind']} is required ({need['rule']})"))
        elif any(g.kind == need["kind"] for g in bp.guardrails) and not any(g.kind == need["kind"] and g.protects for g in bp.guardrails):
            out.append(_v("mandatory-guardrail-idle", ERROR, "guardrails", f"the required {need['kind']} protects nothing"))
    if bp.topology not in decision["allowed_topologies"]:
        out.append(_v("forbidden-topology", ERROR, "topology",
                      f"{bp.topology} is not allowed here; allowed: {', '.join(decision['allowed_topologies'])}"))
    elif bp.topology != decision["topology"]:
        out.append(_v("topology-differs", WARNING, "topology", f"the decision engine chooses {decision['topology']}"))
    if bp.profile.risk == "high" and acting and not any(t.owasp == "LLM06" for t in bp.threats):
        out.append(_v("excessive-agency-unmapped", WARNING, "threats", "map LLM06 (Excessive Agency) for this acting, high-risk blueprint"))
    return _result(out)
