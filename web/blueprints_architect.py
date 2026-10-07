# web/blueprints_architect.py
"""The architect pages and API (spec §4.3, §7). Runs execute on background threads (core.agent.runs.Runner); the
browser follows a run over a same-origin event stream; a waiting run shows its questions; run pages are private
(noindex, no referrer) and everything is deleted after 7 days."""
import hashlib
import re
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse, PlainTextResponse
from pydantic import BaseModel, Field

from blueprints import living
from blueprints.architect import clarify, render
from blueprints.architect.runs import STORE, WAIT_SECONDS, LimitError
from blueprints.cost import load_pricing
from blueprints.patterns import load_patterns
from core.agent.stream import follow, response as stream_response
from core.db import connect
from web.blueprints_site import blueprints_only
from web.security import client_ip, same_origin

router = APIRouter(dependencies=[Depends(blueprints_only)])
RUN_ID = re.compile(r"[0-9a-f]{32}")
STREAM_SECONDS = 180
FINISHED = ("passed", "not_ready")
PRIVATE = {"X-Robots-Tag": "noindex", "Referrer-Policy": "no-referrer"}
STEPS = [("clarify", "Clarify"), ("decide", "Decide"), ("ground", "Ground"), ("candidates", "Two candidates"),
         ("capacity", "Capacity"), ("record", "Decision record"), ("check", "Check"), ("fix", "Fix"),
         ("redteam", "Red team"), ("review", "Architect review"), ("finish", "Finish")]
HOW = [("Describe it", "A few sentences about what the system should do. The agent asks only the questions that change the design."),
       ("It designs and checks", "Two candidate designs, a capacity model, a decision record and runbook; the linter and consistency checks must come back clean."),
       ("Red team and review", "An adversarial pass, then an independent architect review that must score every area production-ready.")]
EXAMPLES = [{"label": "Support agent with refunds",
             "text": "A support agent for an online shop that answers order questions from our help articles and can issue refunds up to $200."},
            {"label": "Contract review", "text": "Reviews supplier contracts for a legal team, flags risky clauses and suggests edits. Lawyers make every final decision."},
            {"label": "Research digest", "text": "Every night, reads new papers and blog posts on a topic and writes a short digest with links for an engineering team."}]


class StartRun(BaseModel):
    description: str = Field(min_length=1, max_length=2000)
    consent: bool


class Answers(BaseModel):
    answers: dict[str, int] = Field(default_factory=dict)


class Refine(BaseModel):
    instruction: str = Field(min_length=1, max_length=300)


def sources(app) -> dict:
    s = app.state
    pricing = load_pricing()
    return {"lint_context": {"trust_grades": living.grades_from_index(s.trust_index.index()),
                             "frameworks": set(s.radar_packages.packages()), "models": set(pricing["models"])},
            "pricing": pricing, "index_rows": s.blueprints_data.index()["blueprints"], "entry_for": s.blueprints_data.entry,
            "patterns": load_patterns()}


def _db(request: Request):
    conn = connect(request.app.state.settings.db_path)
    try:
        STORE.ensure_schema(conn)
        yield conn
    finally:
        conn.close()


def _ip_hash(request: Request) -> str:
    return hashlib.sha256(client_ip(request, request.app.state.settings.trust_proxy).encode()).hexdigest()[:32]


def _guard(request: Request) -> None:
    if not same_origin(request):
        raise HTTPException(403, "cross-site request refused")


def _housekeep(conn, now: float) -> None:
    STORE.expire(conn, now)
    STORE.expire_waiting(conn, now, WAIT_SECONDS)


def _run_or_404(conn, run_id: str) -> dict:
    run = STORE.get_run(conn, run_id) if RUN_ID.fullmatch(run_id) else None
    if run is None:
        raise HTTPException(404)
    return run


@router.get("/architect", response_class=HTMLResponse)
def architect_page(request: Request, conn=Depends(_db)):
    from web.app import render as page
    cfg = request.app.state.architect_config
    available = cfg.enabled and STORE.is_enabled(conn) and STORE.day_spend(conn, time.time()) < cfg.daily_usd
    return page(request, "blueprints_architect.html", available=available, examples=EXAMPLES, how=HOW,
                free_model=bool(cfg.public_models))


@router.post("/api/architect/runs")
def start_run(request: Request, body: StartRun, conn=Depends(_db)):
    _guard(request)
    if not body.consent:
        raise HTTPException(400, "Tick the box to confirm the description is not confidential.")
    text = body.description.strip()
    if not text:
        raise HTTPException(400, "Describe the system you want designed.")
    now = time.time()
    _housekeep(conn, now)
    try:
        rid = STORE.create_checked(conn, request.app.state.architect_config,
                                   {"phase": "clarify", "description": text, "consent": True, "consent_at": now},
                                   "new", _ip_hash(request), now)
    except LimitError as exc:
        raise HTTPException(exc.status, str(exc))
    request.app.state.architect_runner.start(rid)
    return {"id": rid}


@router.post("/api/architect/runs/{run_id}/answers")
def answer(run_id: str, request: Request, body: Answers, conn=Depends(_db)):
    _guard(request)
    run = _run_or_404(conn, run_id)
    if run["status"] != "waiting":
        raise HTTPException(409, "This design is not waiting for answers.")
    r = run["result"] or {}
    try:
        profile, assumptions = clarify.apply_answers(r["profile"], r["known"], r["questions"], body.answers)
    except ValueError as exc:
        raise HTTPException(400, str(exc))
    inp = {**run["input"], "phase": "design", "title": r["title"], "use_case": r["use_case"], "profile": profile,
           "assumptions": assumptions}
    if not STORE.requeue(conn, run_id, inp):
        raise HTTPException(409, "This design is not waiting for answers.")
    request.app.state.architect_runner.start(run_id)
    return {"id": run_id}


@router.post("/api/architect/runs/{run_id}/refine")
def refine(run_id: str, request: Request, body: Refine, conn=Depends(_db)):
    _guard(request)
    run = _run_or_404(conn, run_id)
    r = run["result"] or {}
    if run["status"] not in FINISHED or not r.get("blueprint"):
        raise HTTPException(409, "Only a finished design can be refined.")
    now = time.time()
    _housekeep(conn, now)
    inp = {"phase": "design", "consent": run["input"].get("consent") is True, "title": r["brief"]["title"],
           "use_case": r["brief"]["use_case"], "profile": r["brief"]["profile"], "assumptions": r.get("assumptions") or [],
           "previous": r["blueprint"], "instruction": body.instruction.strip(), "parent": run_id}
    try:
        rid = STORE.create_checked(conn, request.app.state.architect_config, inp, "refine", _ip_hash(request), now)
    except LimitError as exc:
        raise HTTPException(exc.status, str(exc))
    request.app.state.architect_runner.start(rid)
    return {"id": rid}


@router.get("/api/architect/runs/{run_id}/events")
async def events(run_id: str, request: Request, after: int = 0):
    db_path = request.app.state.settings.db_path
    conn = connect(db_path)
    try:
        STORE.ensure_schema(conn)
        _run_or_404(conn, run_id)
    finally:
        conn.close()
    header = request.headers.get("last-event-id", "")
    last = max(after, int(header)) if header.isdigit() else after
    return stream_response(follow(db_path, STORE, run_id, last, STREAM_SECONDS))


def _passed_result(conn, run_id: str) -> dict:
    run = _run_or_404(conn, run_id)
    if run["status"] != "passed":
        raise HTTPException(404)
    return run["result"]


@router.get("/architect/r/{run_id}.md", response_class=PlainTextResponse)
def download_markdown(run_id: str, conn=Depends(_db)):
    return PlainTextResponse(render.markdown(_passed_result(conn, run_id)), media_type="text/markdown",
                             headers={**PRIVATE, "Content-Disposition": f'attachment; filename="design-{run_id[:8]}.md"'})


@router.get("/architect/r/{run_id}.json")
def download_json(run_id: str, conn=Depends(_db)):
    return JSONResponse(render.export(_passed_result(conn, run_id)),
                        headers={**PRIVATE, "Content-Disposition": f'attachment; filename="design-{run_id[:8]}.json"'})


@router.get("/architect/r/{run_id}", response_class=HTMLResponse)
def run_page(run_id: str, request: Request, conn=Depends(_db)):
    from web.app import render as page
    _housekeep(conn, time.time())
    run = _run_or_404(conn, run_id)
    log = STORE.events(conn, run_id)
    message = next((e["payload"].get("text") for e in reversed(log) if e["type"] == "error"), "This design stopped.")
    response = page(request, "blueprints_architect_run.html", run=run, steps=STEPS, events=log, message=message,
                    run_cap=request.app.state.architect_config.run_usd,
                    view=render.view(run["result"]) if run["status"] in FINISHED else None)
    response.headers.update(PRIVATE)
    return response
