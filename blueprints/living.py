"""Living links (spec §7.6): Trust grades and Radar package facts read from published JSON, a snapshot of them for
each blueprint, and the reaction to trust.grade_changed / radar.breaking_change_detected events."""
import json

from blueprints.cost import load_pricing
from core import events, jobs
from core.db import tx
from core.publish import load_current

GENERATE_JOB = "blueprints.generate"
REACT_CONSUMER = "blueprints.living"
REACT_PRIORITY = 3.0
GRADES = ("A", "B", "C", "D", "F")
RECOMMENDED = ("A", "B")


def _read(data_root, planet: str, name: str, file: str, default):
    release = load_current(data_root, planet, name)
    if release is None or file not in release.manifest.get("files", {}):
        return default
    try:
        return json.loads(release.read(file))
    except (OSError, ValueError):
        return default


def grades_from_index(index) -> dict[str, str]:
    servers = index.get("servers") if isinstance(index, dict) else None
    return {s["repo"].lower(): s["grade"] for s in (servers if isinstance(servers, list) else [])
            if isinstance(s, dict) and isinstance(s.get("repo"), str) and s.get("grade") in GRADES}


def load_trust_grades(data_root) -> dict[str, str]:
    return grades_from_index(_read(data_root, "trust", "directory", "index.json", {}))


def packages_from(data) -> dict:
    packages = data.get("packages") if isinstance(data, dict) else None
    return packages if isinstance(packages, dict) else {}


def load_radar_packages(data_root) -> dict:
    return packages_from(_read(data_root, "radar", "digests", "packages.json", {}))


def context(data_root) -> dict:
    return {"trust_grades": load_trust_grades(data_root), "frameworks": set(load_radar_packages(data_root)),
            "models": set(load_pricing()["models"])}


def references(blueprint: dict) -> dict:
    comps = [c for c in blueprint.get("components", []) if isinstance(c, dict)]
    return {"tools": {c["tool_ref"].lower() for c in comps if c.get("tool_ref")},
            "packages": {c["framework_ref"] for c in comps if c.get("framework_ref")}}


def snapshot(blueprint: dict, grades: dict, packages: dict) -> dict:
    tools, frameworks = [], []
    for c in blueprint.get("components", []):
        if not isinstance(c, dict):
            continue
        if c.get("tool_ref"):
            grade = grades.get(c["tool_ref"].lower())
            tools.append({"component": c["id"], "tool_ref": c["tool_ref"], "grade": grade, "recommended": grade in RECOMMENDED})
        if c.get("framework_ref"):
            facts = packages.get(c["framework_ref"]) or {}
            frameworks.append({"component": c["id"], "framework_ref": c["framework_ref"], "tracked": bool(facts),
                               "latest_stable": facts.get("latest_stable"), "advisories": facts.get("advisories", []),
                               "last_breaking": facts.get("last_breaking")})
    return {"tools": tools, "frameworks": frameworks}


def react(conn, now: float) -> dict:
    from blueprints import store                      # local import: store imports nothing from here
    rows = store.approved(conn)
    refreshes: list[dict] = []

    def request(blueprint_id: str, reason: str, event_id: int) -> None:
        with tx(conn):                                   # the refresh, its event and its job exist together or not at all
            _request(blueprint_id, reason, event_id)

    def _request(blueprint_id: str, reason: str, event_id: int) -> None:
        if not store.add_refresh(conn, blueprint_id, reason, event_id, now):
            return
        events.emit(conn, "blueprint.needs_refresh",
                    {"blueprint_id": blueprint_id, "reason": reason, "cause_event_id": event_id}, now=now)
        waiting = conn.execute("SELECT 1 FROM jobs WHERE job_type=? AND state='queued' AND json_extract(payload, '$.id')=?",
                               (GENERATE_JOB, blueprint_id)).fetchone()
        if not waiting:                                  # a job that has not started yet will see every refresh recorded so far
            jobs.enqueue(conn, "blueprints", GENERATE_JOB, f"refresh:{blueprint_id}:{event_id}",
                         {"id": blueprint_id, "reason": reason, "cause_event_id": event_id}, priority=REACT_PRIORITY, now=now)
        refreshes.append({"id": blueprint_id, "reason": reason, "cause_event_id": event_id})

    def handle(event) -> None:
        payload = event.payload
        if event.type == "trust.grade_changed":
            repo, new, old = str(payload.get("server_id", "")).lower(), payload.get("new_grade"), payload.get("old_grade")
            if new in RECOMMENDED:
                return
            reason = f"{repo} is now graded {new or 'ungraded'} (was {old})"
            for row in rows:
                if repo in references(row["body"])["tools"]:
                    request(row["id"], reason, event.id)
        elif event.type == "radar.breaking_change_detected":
            package = payload.get("package")
            reason = f"{package} {payload.get('from_version')} to {payload.get('to_version')} has breaking API changes"
            for row in rows:
                if package in references(row["body"])["packages"]:
                    request(row["id"], reason, event.id)

    handled = events.consume(conn, REACT_CONSUMER, handle,
                             types=["trust.grade_changed", "radar.breaking_change_detected"], limit=500)
    return {"events": handled, "refreshes": refreshes}


def surface_from(data) -> dict:
    packages = data.get("packages") if isinstance(data, dict) else None
    out = {}
    for key, entry in (packages.items() if isinstance(packages, dict) else []):
        if isinstance(entry, dict) and isinstance(entry.get("version"), str) and isinstance(entry.get("symbols"), list):
            out[key] = {"version": entry["version"], "symbols": {s for s in entry["symbols"] if isinstance(s, str)}}
    return out


def load_radar_surface(data_root) -> dict:
    return surface_from(_read(data_root, "radar", "digests", "surface.json", {}))
