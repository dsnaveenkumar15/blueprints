"""Publish approved blueprints as a hash-manifested release. Unapproved and rejected drafts never reach it."""
import hashlib
import json

from blueprints import decision as decision_module
from blueprints import linter, living, store
from blueprints.cost import estimate, is_stale, load_pricing
from blueprints.decision import decide
from blueprints.diagram import to_mermaid
from blueprints.owasp import OWASP_LLM, OWASP_SOURCE, OWASP_VERSION
from blueprints.patterns import load_patterns
from blueprints.starter import build_starter
from core.publish import load_current, prune, publish

DATASET = "library"
KEEP_RELEASES = 10


def _dump(obj) -> bytes:
    return json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False).encode("utf-8")


def lint_context(grades: dict, packages: dict, pricing: dict) -> dict:
    return {"trust_grades": grades, "frameworks": set(packages), "models": set(pricing["models"])}


def _entry(row, pending: dict, grades: dict, packages: dict, pricing: dict, now: float, context: dict,
           surface: dict | None = None) -> tuple[dict, dict]:
    body = row["body"]
    lint_now = linter.lint(body, context)
    cost = estimate(body, pricing, now)
    entry = {"id": body["id"], "title": body["title"], "summary": body["summary"], "use_case": body["use_case"],
             "version": body["version"], "topology": body["topology"], "risk": body["profile"]["risk"],
             "patterns": body["patterns"], "updated_at": row["decided_at"], "source": row["source"],
             "reason": row["reason"], "provenance": row["provenance"], "refresh_pending": pending.get(row["id"]),
             "body": body, "lint": row["lint"], "lint_now": lint_now, "decision": decide(body["profile"]), "cost": cost,
             "diagram": to_mermaid(body), "living": living.snapshot(body, grades, packages),
             "starter": build_starter(body, surface or {})}
    summary = {"id": body["id"], "title": body["title"], "summary": body["summary"], "use_case": body["use_case"],
               "topology": body["topology"], "risk": body["profile"]["risk"], "patterns": body["patterns"],
               "version": body["version"], "refresh_pending": row["id"] in pending, "lint_ok_now": lint_now["ok"],
               "monthly_usd": cost["monthly_usd"], "profile": body["profile"]}
    return entry, summary


def build_files(conn, now: float, grades: dict, packages: dict, pricing: dict, surface: dict | None = None) -> dict[str, bytes]:
    pending = store.pending_refresh(conn)
    context = lint_context(grades, packages, pricing)
    files, index, skipped = {}, [], []
    for row in store.approved(conn):
        try:                                           # one bad stored row must not block the whole release
            entry, summary = _entry(row, pending, grades, packages, pricing, now, context, surface)
        except Exception as exc:
            skipped.append({"id": row["id"], "error": f"{type(exc).__name__}: {exc}"[:200]})
            continue
        files[f"blueprints/{entry['id']}.json"] = _dump(entry)
        index.append(summary)
    files["index.json"] = _dump({"count": len(index), "blueprints": index, "skipped": skipped})
    files["patterns.json"] = _dump(load_patterns())
    files["methodology.json"] = _dump({
        "owasp": {"version": OWASP_VERSION, "source": OWASP_SOURCE, "items": OWASP_LLM},
        "lint_rules": linter.RULES, "decision_rules": decision_module.RULES,
        "pricing": {"as_of": pricing["as_of"], "source": pricing["source"], "stale": is_stale(pricing, now),
                    "latency_status": pricing.get("latency_status", "assumed"),
                    "models": {name: {"label": m.get("label", name), "input_per_mtok": m["input_per_mtok"],
                                      "output_per_mtok": m["output_per_mtok"]} for name, m in pricing["models"].items()}}})
    return files


def _unchanged(files: dict[str, bytes], release) -> bool:
    current = release.manifest.get("files", {})
    return set(current) == set(files) and all(current[n] == hashlib.sha256(d).hexdigest() for n, d in files.items())


def publish_library(conn, data_root, now: float, pricing=None, grades=None, packages=None, surface=None) -> dict:
    pricing = pricing or load_pricing()
    grades = living.load_trust_grades(data_root) if grades is None else grades
    packages = living.load_radar_packages(data_root) if packages is None else packages
    surface = living.load_radar_surface(data_root) if surface is None else surface
    store.sync_decisions(conn, now, lint_context(grades, packages, pricing))
    files = build_files(conn, now, grades, packages, pricing, surface)
    count = json.loads(files["index.json"])["count"]
    release = load_current(data_root, "blueprints", DATASET)
    if release is not None and _unchanged(files, release):
        return {"published": False, "release": release.id, "count": count}
    release_id = publish(data_root, "blueprints", DATASET, files,
                         {"count": count, "pricing_as_of": pricing["as_of"], "owasp": OWASP_VERSION, "generated_at": now},
                         conn=conn, now=now)
    prune(data_root, "blueprints", DATASET, keep=KEEP_RELEASES)
    return {"published": True, "release": release_id, "count": count}
