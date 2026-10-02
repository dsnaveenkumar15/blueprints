import json

from blueprints.cli import main
from core.worker import JobType, PermanentError
from tests.blueprint_fixtures import good


def test_lint_command_reports_and_sets_the_exit_code(tmp_path, capsys):
    path = tmp_path / "bp.json"
    path.write_text(json.dumps(good()), encoding="utf-8")
    code = main(["lint", str(path), "--data-root", str(tmp_path / "published")])
    out = json.loads(capsys.readouterr().out)
    assert code == 1 and out["ok"] is False and {v["rule"] for v in out["violations"]} == {"tool-not-eligible"}   # nothing graded yet
    bp = good()
    del bp["components"][3]["tool_ref"]
    path.write_text(json.dumps(bp), encoding="utf-8")
    assert main(["lint", str(path), "--data-root", str(tmp_path / "published")]) == 0


def test_seed_command_loads_the_library(tmp_path, capsys):
    code = main(["seed", "--db", str(tmp_path / "t.db")])
    assert code == 0 and len(json.loads(capsys.readouterr().out)["seeded"]) == 4
    assert main(["seed", "--db", str(tmp_path / "t.db")]) == 0
    assert json.loads(capsys.readouterr().out)["seeded"] == []


def test_job_commands_run_the_job_and_print_its_stages(tmp_path, capsys):
    def build(conn, use_llm, data_root):
        return {"blueprints.react": JobType("blueprints", [("react", lambda job, out: {"events": 0, "refreshes": []})])}
    code = main(["react", "--db", str(tmp_path / "t.db")], build=build)
    out = json.loads(capsys.readouterr().out)
    assert code == 0 and out["result"] == "done" and out["stages"]["react"] == {"events": 0, "refreshes": []}


def test_a_failing_job_exits_nonzero(tmp_path, capsys):
    def boom(job, out):
        raise PermanentError("bad config")

    def build(conn, use_llm, data_root):
        return {"blueprints.publish": JobType("blueprints", [("publish", boom)])}
    assert main(["publish", "--db", str(tmp_path / "t.db")], build=build) == 1
    assert "bad config" in json.loads(capsys.readouterr().out)["error"]
