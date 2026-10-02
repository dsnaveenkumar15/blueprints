"""Persistence for blueprints, approvals driven by the owner review queue, refresh requests, briefs and seeds."""
import json
from pathlib import Path

from blueprints.cost import load_pricing
from blueprints.linter import lint
from blueprints.living import GENERATE_JOB
from blueprints.models import Blueprint, Profile
from core import review

HERE = Path(__file__).parent
SCHEMA = HERE / "schema.sql"
APPROVAL_KIND = "blueprint_approval"
HELD = " | held at approval: "


def ensure_schema(conn) -> None:
    conn.executescript(SCHEMA.read_text(encoding="utf-8"))
    if "provenance" not in {r["name"] for r in conn.execute("PRAGMA table_info(blueprints)")}:       # database from before provenance
        conn.execute("ALTER TABLE blueprints ADD COLUMN provenance TEXT NOT NULL DEFAULT '{}'")


def ref(blueprint_id: str, version: int) -> str:
    return f"{blueprint_id}@{version}"


def _row(r) -> dict:
    out = dict(r)
    out["body"], out["lint"], out["provenance"] = json.loads(out["body"]), json.loads(out["lint"]), json.loads(out["provenance"])
    return out


def save(conn, body: dict, lint_result: dict, source: str, reason: str, now: float, status: str = "draft",
         cause_event_id: int | None = None, parent_version: int | None = None, provenance: dict | None = None) -> None:
    if lint_result.get("ok"):                       # store what the schema accepted, not the raw copy it coerced
        body = Blueprint.model_validate(body).model_dump(mode="json", exclude_unset=True)
    conn.execute(
        "INSERT INTO blueprints(id, version, status, body, lint, source, reason, cause_event_id, parent_version, "
        "provenance, created_at, decided_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (body["id"], body["version"], status, json.dumps(body, sort_keys=True), json.dumps(lint_result, sort_keys=True),
         source, reason, cause_event_id, parent_version, json.dumps(provenance or {}, sort_keys=True), now,
         now if status == "approved" else None))


def next_version(conn, blueprint_id: str) -> int:
    row = conn.execute("SELECT max(version) AS v FROM blueprints WHERE id=?", (blueprint_id,)).fetchone()
    return (row["v"] or 0) + 1


def get(conn, blueprint_id: str, version: int) -> dict | None:
    row = conn.execute("SELECT * FROM blueprints WHERE id=? AND version=?", (blueprint_id, version)).fetchone()
    return _row(row) if row else None


def latest(conn, blueprint_id: str) -> dict | None:
    row = conn.execute("SELECT * FROM blueprints WHERE id=? ORDER BY version DESC LIMIT 1", (blueprint_id,)).fetchone()
    return _row(row) if row else None


def approved(conn) -> list[dict]:
    rows = conn.execute(
        "SELECT b.* FROM blueprints b WHERE b.status='approved' AND b.version = "
        "(SELECT max(version) FROM blueprints WHERE id=b.id AND status='approved') ORDER BY b.id").fetchall()
    return [_row(r) for r in rows]


def set_status(conn, blueprint_id: str, version: int, status: str, now: float) -> None:
    conn.execute("UPDATE blueprints SET status=?, decided_at=? WHERE id=? AND version=?",
                 (status, now, blueprint_id, version))


def request_approval(conn, row: dict, now: float, open_issues: list[str] | None = None) -> None:
    body = row["body"]
    review.add(conn, "blueprints", APPROVAL_KIND, ref(row["id"], row["version"]),
               {"id": row["id"], "version": row["version"], "title": body["title"], "summary": body["summary"],
                "topology": body["topology"], "lint_ok": row["lint"]["ok"], "warnings": row["lint"]["warnings"],
                "reason": row["reason"], "open_issues": open_issues or []}, now=now)


def sync_decisions(conn, now: float, context: dict | None = None) -> dict:
    """Apply the owner's decisions. With a context, an approved draft that fails the linter today is held as a draft:
    it publishes once the world allows it again (a tool regains its grade), never before."""
    counts = {"approved": 0, "rejected": 0}
    for r in conn.execute("SELECT id, version FROM blueprints WHERE status='draft'").fetchall():
        entry = review.entry_for(conn, APPROVAL_KIND, ref(r["id"], r["version"]))
        if entry is None or entry["state"] not in counts:
            continue
        row = get(conn, r["id"], r["version"])
        base = row["reason"].split(HELD)[0]
        if entry["state"] == "approved":
            errors = [v for v in lint(row["body"], context)["violations"] if v["severity"] == "error"] if context is not None else []
            if errors:
                rules = ", ".join(sorted({v["rule"] for v in errors}))
                conn.execute("UPDATE blueprints SET reason=? WHERE id=? AND version=?",
                             (f"{base}{HELD}{rules}: {errors[0]['message']}"[:600], r["id"], r["version"]))
                continue
            provenance = {**row["provenance"], "approval_id": entry["id"], "approved_at": now}
            conn.execute("UPDATE blueprints SET reason=?, provenance=? WHERE id=? AND version=?",
                         (base, json.dumps(provenance, sort_keys=True), r["id"], r["version"]))
            resolve_refresh(conn, r["id"], row["created_at"])
        set_status(conn, r["id"], r["version"], entry["state"], now)
        counts[entry["state"]] += 1
    return counts


def add_refresh(conn, blueprint_id: str, reason: str, cause_event_id: int, now: float) -> bool:
    cur = conn.execute("INSERT OR IGNORE INTO blueprint_refresh(id, reason, cause_event_id, created_at) VALUES (?, ?, ?, ?)",
                       (blueprint_id, reason[:300], cause_event_id, now))
    return cur.rowcount == 1


def pending_refresh(conn) -> dict:
    out = {}
    for r in conn.execute("SELECT id, reason, created_at FROM blueprint_refresh WHERE resolved=0 "
                          "ORDER BY created_at, cause_event_id"):
        out[r["id"]] = {"reason": r["reason"], "since": r["created_at"]}
    return out


def resolve_refresh(conn, blueprint_id: str, up_to: float | None = None) -> None:
    """Close the refreshes raised before the approved draft was generated; later ones still need a new version."""
    conn.execute("UPDATE blueprint_refresh SET resolved=1 WHERE id=? AND resolved=0 AND (? IS NULL OR created_at <= ?)",
                 (blueprint_id, up_to, up_to))


def load_briefs(path=None) -> list[dict]:
    briefs = json.loads(Path(path or HERE / "briefs.json").read_text(encoding="utf-8"))
    for b in briefs:
        Profile.model_validate(b["profile"])
    return briefs


def next_brief(conn, briefs: list[dict]) -> dict | None:
    """The next brief with no blueprint yet. One whose generation job died is skipped, so it cannot starve the rest."""
    for b in briefs:
        if conn.execute("SELECT 1 FROM blueprints WHERE id=? LIMIT 1", (b["id"],)).fetchone() is not None:
            continue
        if conn.execute("SELECT 1 FROM jobs WHERE job_type=? AND key=? AND state='dead'", (GENERATE_JOB, f"new:{b['id']}")).fetchone():
            continue
        return b
    return None


def load_seeds(directory=None) -> list[dict]:
    return [json.loads(p.read_text(encoding="utf-8")) for p in sorted(Path(directory or HERE / "library").glob("*.json"))]


def seed(conn, now: float, directory=None) -> list[tuple[str, int]]:
    """Seeds are the owner's own commits: loaded as approved, but only if they pass the linter."""
    context = {"models": set(load_pricing()["models"])}
    added = []
    for body in load_seeds(directory):
        result = lint(body, context)
        if not result["ok"]:
            raise ValueError(f"seed {body.get('id')!r} fails the linter: {result['violations'][0]['message']}")
        if get(conn, body["id"], body["version"]) is None:
            save(conn, body, result, "seed", "seed", now, status="approved", provenance={"source": "seed"})
            added.append((body["id"], body["version"]))
    return added
