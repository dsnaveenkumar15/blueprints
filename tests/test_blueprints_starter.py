import ast
import copy

import pytest

from blueprints.starter import REQUIRED_SYMBOLS, build_starter
from tests.blueprint_fixtures import good

SURFACE = {"pypi:langgraph": {"version": "1.3.0", "symbols": set(REQUIRED_SYMBOLS)}}


def test_a_valid_blueprint_gets_a_pinned_validated_runnable_skeleton():
    s = build_starter(good(), SURFACE)
    assert s["framework"] == "langgraph" and s["version"] == "1.3.0" and s["requirements"].strip() == "langgraph==1.3.0"
    tree = ast.parse(s["app"])
    names = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    assert {"classify", "answer", "await_approval", "run"} <= names
    assert s["symbols"] == sorted(REQUIRED_SYMBOLS) and "does not express every topology" in s["readme"]


def test_the_skeleton_follows_the_flow_order_and_places_the_human_gate_before_the_last_step():
    app = build_starter(good(), SURFACE)["app"]
    order = [line for line in app.splitlines() if line.strip().startswith("graph.add_edge(")]
    assert order == ['graph.add_edge("classify", "await_approval")', 'graph.add_edge("await_approval", "answer")']
    assert 'graph.set_entry_point("classify")' in app and 'graph.set_finish_point("answer")' in app


def test_without_a_human_gate_there_is_no_approval_node():
    bp = good()
    bp["components"] = [c for c in bp["components"] if c["kind"] != "human_gate"]
    app = build_starter(bp, SURFACE)["app"]
    assert "await_approval" not in app and 'graph.add_edge("classify", "answer")' in app


def test_it_is_deterministic():
    assert build_starter(good(), SURFACE) == build_starter(good(), SURFACE)


@pytest.mark.parametrize("surface, reason", [({}, "no API data"), ({"pypi:langgraph": {"version": "1.3.0", "symbols": set()}}, "missing symbols"),
                                             ({"pypi:langgraph": {"version": "1.3.0", "symbols": set(REQUIRED_SYMBOLS) - {"langgraph.graph.StateGraph"}}}, "missing symbols")])
def test_no_starter_when_radar_cannot_vouch_for_the_symbols(surface, reason):
    assert build_starter(good(), surface) == {"unavailable": reason if reason == "no API data" else "the pinned version lacks langgraph.graph.StateGraph"} or \
        "unavailable" in build_starter(good(), surface)


def test_hostile_text_in_names_cannot_change_the_code():
    bp = good()
    bp["flow"][0]["id"] = "classify"
    bp["components"][0]["name"] = 'x"""\nimport os; os.system("boom")\n#'
    bp["components"][0]["role"] = "\\n\\\\   `"
    s = build_starter(bp, SURFACE)
    tree = ast.parse(s["app"])
    assert not any(isinstance(n, (ast.Import, ast.ImportFrom)) and any(a.name == "os" for a in n.names) for n in ast.walk(tree))
    assert "os.system" not in "".join(n.id for n in ast.walk(tree) if isinstance(n, ast.Name))
    assert not any(isinstance(n, ast.Call) and getattr(n.func, "attr", "") == "system" for n in ast.walk(tree))


def test_odd_blueprints_give_unavailable_not_an_exception():
    for body in ({}, {"flow": []}, {"flow": [{"id": "A B", "component": "x"}], "components": []}, None, [], {"flow": "x"}):
        assert "unavailable" in build_starter(body, SURFACE)


def test_step_ids_become_safe_python_names():
    bp = good()
    bp["flow"][0]["id"] = "step-one"
    bp["flow"][1]["id"] = "lambda"
    app = build_starter(bp, SURFACE)["app"]
    ast.parse(app)
    assert "def step_one(" in app and "def lambda_(" in app


def test_the_blueprint_is_not_mutated():
    bp = good()
    before = copy.deepcopy(bp)
    build_starter(bp, SURFACE)
    assert bp == before


@pytest.mark.parametrize("step_id", ["input", "print", "len", "type", "state", "object", "str"])
def test_a_step_named_like_a_builtin_or_a_helper_never_shadows_what_the_skeleton_calls(step_id):
    bp = good()
    bp["flow"][0]["id"] = step_id
    app = build_starter(bp, SURFACE)["app"]
    tree = ast.parse(app)
    defined = {n.name for n in ast.walk(tree) if isinstance(n, ast.FunctionDef)}
    import builtins
    assert not (defined & set(dir(builtins))), defined & set(dir(builtins))
    assert "input(" in app and "await_approval" in defined


def test_a_one_step_gated_flow_keeps_its_approval():
    bp = good()
    bp["flow"] = [bp["flow"][1]]
    app = build_starter(bp, SURFACE)["app"]
    ast.parse(app)
    assert 'graph.add_node("await_approval", await_approval)' in app
    assert 'graph.set_entry_point("await_approval")' in app and 'graph.add_edge("await_approval", "answer")' in app


def test_the_claim_names_what_was_checked():
    s = build_starter(good(), SURFACE)
    assert "graph-building" in s["readme"] and "invoke" in s["readme"] and "not checked" in s["readme"]


def test_starters_run_against_real_langgraph(tmp_path):
    pytest.importorskip("langgraph")
    import subprocess
    import sys
    from blueprints.starter import REQUIRED_SYMBOLS
    from blueprints import store
    surface = {"pypi:langgraph": {"version": "0", "symbols": set(REQUIRED_SYMBOLS)}}
    for seed in store.load_seeds():
        path = tmp_path / f"{seed['id']}.py"
        path.write_text(build_starter(seed, surface)["app"], encoding="utf-8")
        result = subprocess.run([sys.executable, str(path)], input="y\n", capture_output=True, text=True, timeout=60)
        assert result.returncode == 0, (seed["id"], result.stderr[-400:])
