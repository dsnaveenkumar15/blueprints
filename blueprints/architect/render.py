# blueprints/architect/render.py
"""What a finished design looks like outside the graph: the run page's view model and the two downloads. Exports carry
the design, never the visitor's own description."""
AREA_LABELS = {"requirement_fit": "Requirement fit", "topology": "Topology choice", "failure_handling": "Failure handling",
               "security": "Security", "cost_and_scale": "Cost and scale", "evaluation": "Evaluation",
               "observability": "Observability", "rollout": "Rollout"}
EXPORT_KEYS = ("verdict", "reason", "brief", "assumptions", "blueprint", "record", "variant", "capacity", "cost", "review",
               "redteam", "alternatives")


def _cell(v) -> str:
    return str("" if v is None else v).replace("|", "\\|").replace("\n", " ")


def _money(v) -> str:
    return "n/a" if v is None else f"${v:,.2f}"


def view(result: dict) -> dict:
    review = result.get("review") or {}
    return {"monthly": (result.get("cost") or {}).get("monthly_usd"),
            "per_request": (result.get("cost") or {}).get("per_request_usd"),
            "scores": [{"area": AREA_LABELS.get(a, a), "score": s} for a, s in (review.get("scores") or {}).items()],
            "variant_monthly": (result.get("variant_cost") or {}).get("monthly_usd")}


def export(result: dict) -> dict:
    return {k: result.get(k) for k in EXPORT_KEYS}


def _table(header: list[str], rows: list[list]) -> list[str]:
    return ["| " + " | ".join(header) + " |", "|" + "---|" * len(header)] + ["| " + " | ".join(_cell(c) for c in r) + " |" for r in rows]


def markdown(result: dict) -> str:
    b, rec, cap = result.get("blueprint") or {}, result.get("record") or {}, result.get("capacity") or {}
    cost = result.get("cost") or {}
    out = [f"# {_cell(b.get('title') or 'Design')}", "",
           f"**{'Passed review' if result.get('verdict') == 'passed' else 'Not ready'}.** {result.get('reason', '')}", "",
           str(b.get("summary") or ""), "", f"Estimated cost: {_money(cost.get('monthly_usd'))} per month "
           f"({_money(cost.get('per_request_usd'))} per request).", "", "## Requirements", ""]
    out += _table(["field", "value"], [[k, v] for k, v in (b.get("profile") or {}).items()])
    if result.get("assumptions"):
        out += ["", "Assumptions:", *[f"- {a}" for a in result["assumptions"]]]
    out += ["", f"## Design ({b.get('topology')})", "", "### Components", ""]
    out += _table(["id", "kind", "role", "model / tool"],
                  [[c.get("id"), c.get("kind"), c.get("role"), c.get("model") or c.get("tool_ref") or ""] for c in b.get("components") or []])
    out += ["", "### Guardrails", ""] + _table(["id", "kind", "protects", "what it does"],
                                              [[g.get("id"), g.get("kind"), ", ".join(g.get("protects") or []), g.get("description")]
                                               for g in b.get("guardrails") or []])
    out += ["", "### Flow", ""] + _table(["step", "component", "tokens in / out", "calls"],
                                        [[s.get("id"), s.get("component"), f"{s.get('input_tokens')} / {s.get('output_tokens')}", s.get("calls", 1)]
                                         for s in b.get("flow") or []])
    out += ["", "### Failure modes", ""] + _table(["failure", "component", "mitigated by"],
                                                 [[f.get("description"), f.get("component"), f.get("mitigation")] for f in b.get("failure_modes") or []])
    out += ["", "### Threats", ""] + _table(["OWASP", "component", "mitigated by", "note"],
                                           [[t.get("owasp"), t.get("component"), t.get("mitigation"), t.get("note")] for t in b.get("threats") or []])
    out += ["", "### Evaluation", ""] + _table(["metric", "method", "threshold"],
                                              [[e.get("metric"), e.get("method"), e.get("threshold")] for e in b.get("eval_plan") or []])
    out += ["", "### Observability", "", *[f"- {o}" for o in b.get("observability") or []]]
    out += ["", f"Deployment: {(b.get('deployment') or {}).get('runtime', '')}", "", "### Diagram", "", "```mermaid",
            str(result.get("diagram") or ""), "```"]
    if rec:
        out += ["", "## Decision record", "", f"**Context.** {rec['context']}", "", f"**Decision.** {rec['decision']}", "",
                "**Alternatives considered:**", *[f"- {a['topology']}: {a['rejected_because']}" for a in rec["alternatives"]],
                "", "**Trade-offs:**", *[f"- {t}" for t in rec["trade_offs"]], "",
                f"**Targets.** p95 latency {rec['targets']['latency_p95_s']:g} s; availability {rec['targets']['availability_pct']:g}%; "
                f"quality: {rec['targets']['quality']}", "", f"**Scaling.** {rec['scaling']}", "",
                "**Rollout:**", *[f"{i}. {s}" for i, s in enumerate(rec["rollout"], 1)], "", f"**Rollback.** {rec['rollback']}"]
        if rec["open_risks"]:
            out += ["", "**Open risks:**", *[f"- {r['text']} (failure mode {r['failure_mode']})" for r in rec["open_risks"]]]
    if cap:
        out += ["", "## Capacity", "", f"Latency {cap['latency_s']:g} s against a {cap['latency_target_s']:g} s target. "
                f"{cap['first_limit']['text']}", ""]
        out += _table(["volume", "requests/day", "peak req/s", "concurrency", "per month"],
                      [[f"{s['scale']}x", f"{s['volume_per_day']:,}", s["peak_rps"], s["concurrency"], _money(s["monthly_usd"])]
                       for s in cap["scales"]])
    if rec:
        out += ["", "## Runbook", ""] + _table(["failure mode", "alert", "meaning", "first action"],
                                              [[r["failure_mode"], r["alert"], r["meaning"], r["first_action"]] for r in rec["runbook"]])
    review = result.get("review")
    if review:
        out += ["", "## Review", ""] + _table(["area", "score"], [[AREA_LABELS.get(a, a), f"{s} / 5"] for a, s in review["scores"].items()])
        out += ["", review.get("summary") or ""]
    variant = result.get("variant")
    if variant:
        out += ["", "## If the budget is tighter", "", variant["summary"], *[f"- {c}" for c in variant["changes"]], "",
                f"Estimated cost: {_money((result.get('variant_cost') or {}).get('monthly_usd'))} per month."]
    return "\n".join(out) + "\n"
