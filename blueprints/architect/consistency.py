# blueprints/architect/consistency.py
"""Cross-checks between the decision record, the capacity model and the blueprint (spec §5.3), plus the linter's
findings in the same problem format. Pure code; an empty list means the design agrees with itself."""
import re

from blueprints.architect.candidates import ALLOWED_WARNING
from blueprints.cost import estimate
from blueprints.linter import lint
from blueprints.models import HIGH_RISK_CAPABILITIES

SIGNAL = re.compile(r"\d|latenc|cost|spend|budget|error|rate|trace|log|metric|count|ratio|accuracy|token|throughput|"
                    r"queue|drift|alert|duration|percent|score|p9\d", re.I)


RECORD_RULES = [
    "targets.latency_p95_s is at least the computed flow latency in 'capacity'",
    "every open_risks and runbook entry names a failure_mode id that exists in the blueprint",
    "the scaling text contains no digits or numbers: the capacity table is shown beside it",
    "every component with a high-risk capability (write, exec, network, payment) has a threat that names it",
    "every eval_plan threshold contains a number",
    "every observability item names a metric, log, trace or alert signal",
]


def _p(rule: str, path: str, message: str) -> dict:
    return {"rule": rule, "path": path, "message": message}


def lint_problems(body: dict, context: dict, allow_topology_differs: bool) -> list[dict]:
    result = lint(body, context)
    return [_p(v["rule"], v["path"], v["message"]) for v in result["violations"]
            if not (allow_topology_differs and v["rule"] == ALLOWED_WARNING)]


def check(body: dict, rec: dict | None, cap: dict, variant: dict | None, context: dict, pricing: dict, now: float) -> list[dict]:
    out = []
    modes = {f.get("id") for f in body.get("failure_modes") or [] if isinstance(f, dict)}
    if not rec:
        out.append(_p("record-missing", "record", "The decision record is missing."))
    else:
        if rec["targets"]["latency_p95_s"] < cap["latency_s"]:
            out.append(_p("latency-target", "record.targets.latency_p95_s",
                          f"The latency target {rec['targets']['latency_p95_s']:g} s is below the computed flow latency "
                          f"{cap['latency_s']:g} s; raise the target or make the flow faster."))
        for i, r in enumerate(rec["open_risks"]):
            if r["failure_mode"] not in modes:
                out.append(_p("risk-unmapped", f"record.open_risks.{i}", f"Open risk names failure mode {r['failure_mode']!r}, "
                                                                         "which the blueprint does not have."))
        for i, r in enumerate(rec["runbook"]):
            if r["failure_mode"] not in modes:
                out.append(_p("runbook-unmapped", f"record.runbook.{i}", f"Runbook entry names failure mode {r['failure_mode']!r}, "
                                                                          "which the blueprint does not have."))
        if re.search(r"\d", rec["scaling"]):
            out.append(_p("scaling-numbers", "record.scaling", "The scaling text must not state numbers: the capacity "
                                                               "table beside it carries them."))
    threatened = {t.get("component") for t in body.get("threats") or [] if isinstance(t, dict)}
    for c in body.get("components") or []:
        if isinstance(c, dict) and set(c.get("capabilities") or []) & set(HIGH_RISK_CAPABILITIES) and c.get("id") not in threatened:
            out.append(_p("threat-missing", f"components.{c.get('id')}", f"Component {c.get('id')!r} has a high-risk "
                                                                         "capability but no threat maps to it."))
    for i, e in enumerate(body.get("eval_plan") or []):
        if isinstance(e, dict) and not re.search(r"\d", str(e.get("threshold", ""))):
            out.append(_p("eval-threshold", f"eval_plan.{i}.threshold", "An eval threshold must be a number."))
    for i, o in enumerate(body.get("observability") or []):
        if not SIGNAL.search(str(o)):
            out.append(_p("observability-signal", f"observability.{i}", f"{o!r} names no metric, log or trace signal."))
    if variant:                                   # only when a cheaper variant was asked for
        if any(v["rule"] != ALLOWED_WARNING for v in lint(variant, context)["violations"]):
            out.append(_p("variant-lint", "variant", "The cheaper variant must pass the linter with no errors or warnings."))
        main, lean = estimate(body, pricing, now), estimate(variant, pricing, now)
        if not (main["complete"] and lean["complete"] and lean["monthly_usd"] < main["monthly_usd"]):
            out.append(_p("variant-cost", "variant", "The cheaper variant must cost less per month than the design."))
    return out
