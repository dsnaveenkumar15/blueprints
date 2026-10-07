"""Blueprints' part of the owner admin: the draft preview and the list of approvals the linter holds."""
import json
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Path, Request
from fastapi.responses import PlainTextResponse, RedirectResponse

from blueprints import store as blueprint_store
from blueprints.architect import runs as architect_runs
from blueprints.cost import estimate
from blueprints.decision import decide as decide_topology
from blueprints.diagram import to_mermaid
from blueprints.linter import lint
from core import review
from web.admin import _payload, get_db, require_admin

router = APIRouter(prefix="/admin", dependencies=[Depends(require_admin)])


def section(conn, now: float) -> dict:
    blueprint_store.ensure_schema(conn)
    held = conn.execute("SELECT id, version, reason FROM blueprints WHERE status='draft' AND reason LIKE ? ORDER BY id",
                        ("%" + blueprint_store.HELD.strip(" |") + "%",)).fetchall()
    architect_runs.STORE.ensure_schema(conn)
    cfg = architect_runs.load_config()
    return {"held": held, "architect_spend": architect_runs.STORE.day_spend(conn, now), "architect_cap": cfg.daily_usd,
            "architect_configured": cfg.enabled, "architect_switch": architect_runs.STORE.is_enabled(conn),
            "architect_runs": architect_runs.STORE.recent(conn, 20)}


@router.post("/architect/toggle")
def toggle_architect(conn=Depends(get_db)):
    architect_runs.STORE.ensure_schema(conn)
    architect_runs.STORE.set_enabled(conn, not architect_runs.STORE.is_enabled(conn))
    return RedirectResponse("/admin", status_code=303)


@router.get("/architect/{run_id}/brief", response_class=PlainTextResponse)
def architect_brief(run_id: str, conn=Depends(get_db)):
    """A brief for blueprints/briefs.json built from the requirements profile only: the owner names it."""
    architect_runs.STORE.ensure_schema(conn)
    run = architect_runs.STORE.get_run(conn, run_id) if re.fullmatch(r"[0-9a-f]{32}", run_id) else None
    if run is None or run["status"] != "passed":
        raise HTTPException(404)
    brief = {"id": "rename-me", "title": "Name this blueprint", "use_case": "set a use case",
             "profile": run["result"]["brief"]["profile"]}
    return PlainTextResponse(json.dumps(brief, indent=1) + "\n")


@router.get("/blueprints/{blueprint_id}/{version}")
def blueprint_preview(blueprint_id: str, request: Request, version: int = Path(ge=1, le=10_000), conn=Depends(get_db)):
    from web.app import render  # local import: web.app includes this router
    from web.blueprints_api import lint_context, pricing
    blueprint_store.ensure_schema(conn)
    row = blueprint_store.get(conn, blueprint_id, version)
    if row is None:
        raise HTTPException(404)
    body = row["body"] if isinstance(row["body"], dict) else {}
    today = lint(body, lint_context(request))
    decision = cost = diagram = None
    for name, compute in (("decision", lambda: decide_topology(body["profile"])), ("cost", lambda: estimate(body, pricing(), time.time())),
                          ("diagram", lambda: to_mermaid(body))):
        try:                              # a rejected draft may hold a raw body one engine cannot read: keep what the others can
            if name == "decision":
                decision = compute()
            elif name == "cost":
                cost = compute()
            else:
                diagram = compute()
        except Exception:
            pass
    entry = review.entry_for(conn, blueprint_store.APPROVAL_KIND, blueprint_store.ref(blueprint_id, version))
    issues = _payload(entry).get("open_issues", []) if entry is not None else []
    return render(request, "admin_blueprint.html", row=row, b=body, today=today, decision=decision, cost=cost,
                  diagram=diagram, entry=entry, issues=issues if isinstance(issues, list) else [])
