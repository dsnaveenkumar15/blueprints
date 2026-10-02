"""Blueprints' part of the owner admin: the draft preview and the list of approvals the linter holds."""
import time

from fastapi import APIRouter, Depends, HTTPException, Path, Request

from blueprints import store as blueprint_store
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
    return {"held": held}


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
