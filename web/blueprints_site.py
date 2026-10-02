"""Blueprints planet pages. Data comes only from the current verified 'blueprints/library' release."""
import re

from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import HTMLResponse

from web.published import PublishedData

ID = re.compile(r"[a-z][a-z0-9-]{0,40}")
FILTERS = ("use_case", "risk", "topology")
EMPTY_INDEX = {"count": 0, "blueprints": [], "skipped": []}


def _d(value) -> dict:
    return value if isinstance(value, dict) else {}


def _list(value) -> list:
    return value if isinstance(value, list) else []


def normalise_entry(entry) -> dict | None:
    """Fill every field the templates read, so a sparse entry degrades to a sparse page. Never mutates the cache."""
    if not isinstance(entry, dict) or not isinstance(entry.get("body"), dict):
        return None
    body = entry["body"]
    if not isinstance(body.get("profile"), dict) or not all(isinstance(body.get(k), list)
                                                           for k in ("components", "flow", "failure_modes")):
        return None
    cost = {"per_request_usd": 0, "monthly_usd": None, "latency_s": 0, "volume_per_day": 0, "budget_usd_month": 0,
            "over_budget": False, "steps": [], "complete": False, "unpriced": [], "pricing": {}, "assumptions": [],
            **_d(entry.get("cost"))}
    decision = {"topology": body.get("topology"), "allowed_topologies": [], "mandatory": [], "patterns": [], "trace": [],
                **_d(entry.get("decision"))}
    return {**entry, "cost": cost, "decision": decision,
            "living": {**_d(entry.get("living")), "tools": _list(_d(entry.get("living")).get("tools")),
                       "frameworks": _list(_d(entry.get("living")).get("frameworks"))},
            "patterns": _list(entry.get("patterns")),
            "lint_now": {"ok": True, "errors": 0, "warnings": 0, "violations": [], **_d(entry.get("lint_now"))},
            "provenance": _d(entry.get("provenance")), "starter": _d(entry.get("starter")), "diagram": str(entry.get("diagram") or ""),
            "refresh_pending": _d(entry.get("refresh_pending")) or None}


class BlueprintsData(PublishedData):
    PLANET, DATASET = "blueprints", "library"

    def index(self) -> dict:
        data = self._json("index.json", EMPTY_INDEX)
        if not isinstance(data, dict) or not isinstance(data.get("blueprints"), list):
            return EMPTY_INDEX
        rows = [b for b in data["blueprints"] if isinstance(b, dict) and ID.fullmatch(str(b.get("id", "")))]
        return {"count": len(rows), "blueprints": rows, "skipped": [s for s in data.get("skipped") or [] if isinstance(s, dict)]}

    def entry(self, blueprint_id: str) -> dict | None:
        if not ID.fullmatch(blueprint_id):
            return None
        return normalise_entry(self._json(f"blueprints/{blueprint_id}.json", None))

    def patterns(self) -> list[dict]:
        data = self._json("patterns.json", [])
        return [p for p in data if isinstance(p, dict)] if isinstance(data, list) else []

    def methodology(self) -> dict:
        return _d(self._json("methodology.json", {}))


def blueprints_only(request: Request) -> None:
    if request.state.planet != "blueprints":
        raise HTTPException(404)


router = APIRouter(dependencies=[Depends(blueprints_only)])


def home(request: Request):
    from web.app import render   # local import: web.app includes this router
    index = request.app.state.blueprints_data.index()
    rows = index["blueprints"]
    options = {f: sorted({str(b[f]) for b in rows if b.get(f)}) for f in FILTERS}
    chosen = {f: request.query_params.get(f, "") for f in FILTERS}
    shown = [b for b in rows if all(not chosen[f] or str(b.get(f)) == chosen[f] for f in FILTERS)]
    return render(request, "blueprints_library.html", rows=shown, choices=rows, total=len(rows), options=options, chosen=chosen,
                  skipped=len(index["skipped"]))


@router.get("/b/{blueprint_id}", response_class=HTMLResponse)
def detail(blueprint_id: str, request: Request):
    from web.app import render
    data = request.app.state.blueprints_data
    entry = data.entry(blueprint_id)
    if entry is None:
        raise HTTPException(404)
    body = entry["body"]
    living = entry["living"]
    return render(request, "blueprints_detail.html", e=entry, b=body,
                  tools={t["component"]: t for t in living["tools"] if isinstance(t, dict) and "component" in t},
                  frameworks={f["component"]: f for f in living["frameworks"] if isinstance(f, dict) and "component" in f},
                  names={c.get("id"): c.get("name") for c in body["components"] if isinstance(c, dict)},
                  owasp=_d(_d(data.methodology().get("owasp")).get("items")), cost_data=cost_data(entry["cost"]))


@router.get("/patterns", response_class=HTMLResponse)
def patterns_page(request: Request):
    from web.app import render
    return render(request, "blueprints_patterns.html", patterns=request.app.state.blueprints_data.patterns())


def cost_data(cost: dict) -> dict:
    """What the slider script needs: priced steps with their inputs, and whether the estimate is complete."""
    steps = [s["inputs"] for s in cost["steps"] if isinstance(s, dict) and isinstance(s.get("inputs"), dict)]
    return {"steps": steps, "complete": bool(cost["complete"]) and len(steps) == len(cost["steps"]),
            "volume": cost["volume_per_day"], "budget": cost["budget_usd_month"]}


@router.get("/finder", response_class=HTMLResponse)
def finder_page(request: Request):
    from web.app import render
    return render(request, "blueprints_finder.html")


STARTER_FILES = {"app.py": "app", "requirements.txt": "requirements", "README.md": "readme"}


@router.get("/b/{blueprint_id}/starter/{name}")
def starter_file(blueprint_id: str, name: str, request: Request):
    from fastapi.responses import PlainTextResponse
    entry = request.app.state.blueprints_data.entry(blueprint_id)
    key = STARTER_FILES.get(name)
    if entry is None or key is None or key not in entry["starter"]:
        raise HTTPException(404)
    return PlainTextResponse(str(entry["starter"][key]), headers={"Content-Disposition": f'attachment; filename="{name}"'})


def _money(value) -> str:
    return f"${value:.2f}" if isinstance(value, (int, float)) else "n/a"


def _count(body: dict, key: str) -> str:
    value = body.get(key)
    return str(len(value)) if isinstance(value, list) else "0"


def compare_rows(a: dict, b: dict) -> list[dict]:
    def facts(e: dict) -> list[tuple[str, str]]:
        body, cost, profile = e["body"], e["cost"], e["body"].get("profile") or {}
        tools = e["living"]["tools"]
        return [("Use case", str(e.get("use_case", ""))), ("Topology", str(e.get("topology", ""))), ("Risk", str(e.get("risk", ""))),
                ("Data", str(profile.get("data_sensitivity", ""))), ("Autonomy", str(profile.get("autonomy", ""))),
                ("Latency need", str(profile.get("latency", ""))), ("Requests per day", str(profile.get("volume_per_day", ""))),
                ("Patterns", ", ".join(map(str, e.get("patterns") or []))),
                ("Components", _count(body, "components")), ("Guardrails", _count(body, "guardrails")),
                ("Failure modes", _count(body, "failure_modes")), ("Threats mapped", _count(body, "threats")),
                ("Tools not graded A or B", str(sum(1 for t in tools if isinstance(t, dict) and not t.get("recommended")))),
                ("Per request", f"${cost['per_request_usd']:.4f}" if cost["complete"] and isinstance(cost.get("per_request_usd"), (int, float)) else "n/a"),
                ("Monthly cost", _money(cost.get("monthly_usd")) if cost["complete"] else "n/a"),
                ("Latency", f"{cost['latency_s']} s" if cost["complete"] else "n/a"),
                ("Fails linter today", "no" if e["lint_now"].get("ok", True) else "yes"), ("Version", str(e.get("version", "")))]
    return [{"label": la, "a": va, "b": vb, "differs": va != vb} for (la, va), (_, vb) in zip(facts(a), facts(b))]


@router.get("/compare", response_class=HTMLResponse)
def compare_page(request: Request):
    from web.app import render
    data = request.app.state.blueprints_data
    ids = {k: request.query_params.get(k, "") for k in ("a", "b")}
    rows = data.index()["blueprints"]
    if not ids["a"] or not ids["b"]:
        return render(request, "blueprints_compare.html", pair=None, rows=rows, ids=ids)
    a, b = data.entry(ids["a"]), data.entry(ids["b"])
    if a is None or b is None:
        raise HTTPException(404)
    return render(request, "blueprints_compare.html", pair=(a, b), table=compare_rows(a, b), rows=rows, ids=ids)


@router.get("/custom", response_class=HTMLResponse)
def custom_page(request: Request):
    from web.app import render
    from web.blueprints_api import BYOK_MODELS   # local import: blueprints_api imports this module
    return render(request, "blueprints_custom.html", byok_models=BYOK_MODELS)
