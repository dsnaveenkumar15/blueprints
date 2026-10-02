"""Living links (spec §7.6): Trust grades and Radar package facts read from published JSON, a snapshot of them for
each blueprint, and the reaction to trust.grade_changed / radar.breaking_change_detected events."""
import hashlib
import json
import os

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


def _root(data_root, env_name: str):
    """Trust's and Radar's data may live elsewhere when the apps are separate repos: TRUST_DATA_ROOT / RADAR_DATA_ROOT."""
    return os.environ.get(env_name) or data_root


def grades_from_index(index) -> dict[str, str]:
    servers = index.get("servers") if isinstance(index, dict) else None
    return {s["repo"].lower(): s["grade"] for s in (servers if isinstance(servers, list) else [])
            if isinstance(s, dict) and isinstance(s.get("repo"), str) and s.get("grade") in GRADES}


def load_trust_grades(data_root) -> dict[str, str]:
    return grades_from_index(_read(_root(data_root, "TRUST_DATA_ROOT"), "trust", "directory", "index.json", {}))


def packages_from(data) -> dict:
    packages = data.get("packages") if isinstance(data, dict) else None
    return packages if isinstance(packages, dict) else {}


def load_radar_packages(data_root) -> dict:
    return packages_from(_read(_root(data_root, "RADAR_DATA_ROOT"), "radar", "digests", "packages.json", {}))


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


def _request(conn, now: float, blueprint_id: str, reason: str, cause_id: int, refreshes: list) -> None:
    """Record a refresh request, announce it and queue one regeneration, all or nothing. A cause already pending (the event path
    and the published-data path often see the same change) is not requested twice."""
    from blueprints import store                      # local import: store imports nothing from here
    with tx(conn):
        if conn.execute("SELECT 1 FROM blueprint_refresh WHERE id=? AND reason=? AND resolved=0", (blueprint_id, reason[:300])).fetchone():
            return
        if not store.add_refresh(conn, blueprint_id, reason, cause_id, now):
            return
        events.emit(conn, "blueprint.needs_refresh", {"blueprint_id": blueprint_id, "reason": reason, "cause_event_id": cause_id}, now=now)
        waiting = conn.execute("SELECT 1 FROM jobs WHERE job_type=? AND state='queued' AND json_extract(payload, '$.id')=?",
                               (GENERATE_JOB, blueprint_id)).fetchone()
        if not waiting:                                  # a job that has not started yet will see every refresh recorded so far
            jobs.enqueue(conn, "blueprints", GENERATE_JOB, f"refresh:{blueprint_id}:{cause_id}",
                         {"id": blueprint_id, "reason": reason, "cause_event_id": cause_id}, priority=REACT_PRIORITY, now=now)
        refreshes.append({"id": blueprint_id, "reason": reason, "cause_event_id": cause_id})


def react(conn, now: float) -> dict:
    from blueprints import store
    rows = store.approved(conn)
    refreshes: list[dict] = []

    def handle(event) -> None:
        payload = event.payload
        if event.type == "trust.grade_changed":
            repo, new, old = str(payload.get("server_id", "")).lower(), payload.get("new_grade"), payload.get("old_grade")
            if new in RECOMMENDED:
                return
            reason = f"{repo} is now graded {new or 'ungraded'} (was {old})"
            for row in rows:
                if repo in references(row["body"])["tools"]:
                    _request(conn, now, row["id"], reason, event.id, refreshes)
        elif event.type == "radar.breaking_change_detected":
            package = payload.get("package")
            reason = f"{package} {payload.get('from_version')} to {payload.get('to_version')} has breaking API changes"
            for row in rows:
                if package in references(row["body"])["packages"]:
                    _request(conn, now, row["id"], reason, event.id, refreshes)

    handled = events.consume(conn, REACT_CONSUMER, handle,
                             types=["trust.grade_changed", "radar.breaking_change_detected"], limit=500)
    return {"events": handled, "refreshes": refreshes}


def _watch(conn, key: str):
    row = conn.execute("SELECT value FROM blueprint_watch WHERE key=?", (key,)).fetchone()
    return row["value"] if row else None


def _remember(conn, key: str, value: str) -> None:
    conn.execute("INSERT INTO blueprint_watch(key, value) VALUES (?, ?) ON CONFLICT(key) DO UPDATE SET value=excluded.value", (key, value))


def _cause(key: str, value: str) -> int:
    """A stable negative id for a published-data cause, so it never collides with a real event id."""
    return -(int(hashlib.sha1(f"{key}={value}".encode()).hexdigest()[:8], 16) + 1)


def react_published(conn, now: float, grades: dict, packages: dict) -> dict:
    """The same reactions as react(), noticed by comparing Trust's and Radar's published data with what was seen last time. This is
    how Blueprints learns of changes when the apps do not share a database. The first look is only a baseline; empty data (a root
    that is missing or not yet published) is not a change."""
    from blueprints import store
    rows = store.approved(conn)
    refs = {row["id"]: references(row["body"]) for row in rows}
    changes: list[tuple[str, str, str, int]] = []
    for tool in sorted(set().union(*[r["tools"] for r in refs.values()])) if grades else []:
        grade, key = grades.get(tool, ""), f"grade:{tool}"
        previous = _watch(conn, key)
        if previous != grade:
            _remember(conn, key, grade)
            if previous is not None and grade not in RECOMMENDED:
                changes.append(("tools", tool, f"{tool} is now graded {grade or 'ungraded'} (was {previous or 'ungraded'})", _cause(key, grade)))
    for package in sorted(set().union(*[r["packages"] for r in refs.values()])) if packages else []:
        last = (packages.get(package) or {}).get("last_breaking")
        known = isinstance(last, dict) and last.get("from") and last.get("to")
        value, key = (f"{last['from']} to {last['to']}" if known else ""), f"breaking:{package}"
        previous = _watch(conn, key)
        if previous != value:
            _remember(conn, key, value)
            if previous is not None and value:
                changes.append(("packages", package, f"{package} {value} has breaking API changes", _cause(key, value)))
    refreshes: list[dict] = []
    for kind, name, reason, cause in changes:
        for row in rows:
            if name in refs[row["id"]][kind]:
                _request(conn, now, row["id"], reason, cause, refreshes)
    return {"refreshes": refreshes}


def react_all(conn, now: float, data_root) -> dict:
    """Events first (one shared database), then published data (separate apps); a change seen both ways is requested once."""
    out = react(conn, now)
    published = react_published(conn, now, load_trust_grades(data_root), load_radar_packages(data_root))
    return {"events": out["events"], "refreshes": out["refreshes"] + published["refreshes"]}


def surface_from(data) -> dict:
    packages = data.get("packages") if isinstance(data, dict) else None
    out = {}
    for key, entry in (packages.items() if isinstance(packages, dict) else []):
        if isinstance(entry, dict) and isinstance(entry.get("version"), str) and isinstance(entry.get("symbols"), list):
            out[key] = {"version": entry["version"], "symbols": {s for s in entry["symbols"] if isinstance(s, str)}}
    return out


def load_radar_surface(data_root) -> dict:
    return surface_from(_read(_root(data_root, "RADAR_DATA_ROOT"), "radar", "digests", "surface.json", {}))
