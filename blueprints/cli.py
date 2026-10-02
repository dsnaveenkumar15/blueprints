"""Owner tools: seed | lint FILE | generate ID | react | publish. Job commands run the job once and print its stages."""
import argparse
import json
import os
import time

from blueprints import jobs as blueprint_jobs
from blueprints import living, store
from blueprints.cost import load_pricing
from blueprints.linter import lint
from core.db import connect
from core.jobs import enqueue
from core.worker import run_once

COMMANDS = {"generate": blueprint_jobs.GENERATE_JOB, "react": blueprint_jobs.REACT_JOB, "publish": blueprint_jobs.PUBLISH_JOB}


def main(argv=None, build=None) -> int:
    parser = argparse.ArgumentParser(prog="blueprints.cli")
    parser.add_argument("command", choices=["seed", "lint", *sorted(COMMANDS)])
    parser.add_argument("target", nargs="?", help="a file for lint, a blueprint id for generate")
    parser.add_argument("--db", default=os.environ.get("DB_PATH", "data/planets.db"))
    parser.add_argument("--data-root", default=os.environ.get("DATA_ROOT", "data/published"))
    parser.add_argument("--no-llm", action="store_true")
    args = parser.parse_args(argv)
    if args.command == "lint":
        with open(args.target, encoding="utf-8") as handle:
            result = lint(json.load(handle), living.context(args.data_root))
        print(json.dumps(result, indent=2))
        return 0 if result["ok"] else 1
    conn = connect(args.db)
    store.ensure_schema(conn)
    if args.command == "seed":
        print(json.dumps({"seeded": store.seed(conn, time.time())}))
        return 0
    types = (build or blueprint_jobs.build_job_types)(conn, use_llm=not args.no_llm, data_root=args.data_root)
    now = time.time()
    job_type = COMMANDS[args.command]
    payload = {"id": args.target, "reason": "manual", "cause_event_id": None} if args.command == "generate" else {}
    key = f"cli:{int(now * 1000)}"
    enqueue(conn, "blueprints", job_type, key, payload, priority=100.0, now=now)
    result = run_once(conn, types, {"blueprints": 1.0}, "cli")
    job = conn.execute("SELECT id, state, last_error FROM jobs WHERE job_type=? AND key=?", (job_type, key)).fetchone()
    stages = {r["stage"]: json.loads(r["output"])
              for r in conn.execute("SELECT stage, output FROM stage_artifacts WHERE job_id=?", (job["id"],))}
    print(json.dumps({"result": result, "state": job["state"], "error": job["last_error"], "stages": stages}, indent=2, default=str))
    return 0 if result == "done" else 1


if __name__ == "__main__":
    raise SystemExit(main())
