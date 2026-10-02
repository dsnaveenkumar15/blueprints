"""A runnable LangGraph skeleton generated from a blueprint by code, pinned to the version Radar has an API snapshot of
and offered only when every symbol it uses exists in that snapshot. No model, no network, no LangGraph import."""
import ast
import builtins
import keyword
import re

PACKAGE = "pypi:langgraph"
REQUIRED_SYMBOLS = ("langgraph.graph.StateGraph", "langgraph.graph.StateGraph.add_node", "langgraph.graph.StateGraph.add_edge",
                    "langgraph.graph.StateGraph.set_entry_point", "langgraph.graph.StateGraph.set_finish_point",
                    "langgraph.graph.StateGraph.compile")
_SAFE = re.compile(r"[^A-Za-z0-9_]")
MAX_STEPS = 30


def _ident(raw, used: set[str]) -> str:
    name = _SAFE.sub("_", str(raw)).strip("_") or "step"
    if name[0].isdigit():
        name = "step_" + name
    if keyword.iskeyword(name) or hasattr(builtins, name) or name in {"graph", "app", "run", "State", "call_model", "await_approval", "main", "TypedDict", "StateGraph"}:
        name += "_"
    base, n = name, 2
    while name in used:
        name, n = f"{base}_{n}", n + 1
    used.add(name)
    return name


def _comment(text) -> str:
    return re.sub(r"[^A-Za-z0-9 .,:;()/_@+-]", "", str(text))[:100]


def build_starter(blueprint, surface: dict) -> dict:
    try:
        return _build(blueprint, surface)
    except (KeyError, TypeError, ValueError, AttributeError, SyntaxError):
        return {"unavailable": "the blueprint could not be turned into a skeleton"}


def _build(blueprint, surface: dict) -> dict:
    entry = surface.get(PACKAGE) if isinstance(surface, dict) else None
    if not entry:
        return {"unavailable": "no API data for langgraph from Radar yet"}
    missing = [s for s in REQUIRED_SYMBOLS if s not in entry["symbols"]]
    if missing:
        return {"unavailable": f"langgraph {entry['version']} lacks {missing[0]} in Radar's API snapshot"}
    if not isinstance(blueprint, dict) or not isinstance(blueprint.get("flow"), list) or not blueprint["flow"] \
            or len(blueprint["flow"]) > MAX_STEPS or not isinstance(blueprint.get("components"), list):
        return {"unavailable": "the blueprint has no flow to build from"}
    components = {c["id"]: c for c in blueprint["components"] if isinstance(c, dict) and "id" in c}
    gated = any(isinstance(c, dict) and c.get("kind") == "human_gate" for c in blueprint["components"])
    used: set[str] = set()
    steps = []
    for step in blueprint["flow"]:
        comp = components[step["component"]]
        steps.append((_ident(step["id"], used), _comment(comp.get("name") or step["component"]), _comment(comp.get("model") or "")))
    lines = ['"""Skeleton generated from the blueprint. It runs offline: model calls are stubs you replace."""',
             "from typing import TypedDict", "", "from langgraph.graph import StateGraph", "", "",
             "class State(TypedDict):", "    request: str", "    trace: list", "", "",
             "def call_model(step: str, model: str, state: State) -> str:",
             "    # Replace with your model client. Keep the step name and model for tracing.",
             '    return f"[{step} via {model or \'a model\'}] {state[\'request\']}"', ""]
    for name, label, model in steps:
        lines += ["", f"def {name}(state: State) -> State:", f"    # {label}" + (f" ({model})" if model else ""),
                  f"    output = call_model({name!r}, {model!r}, state)",
                  f'    return {{"request": output, "trace": state["trace"] + [{name!r}]}}', ""]
    if gated:
        lines += ["", "def await_approval(state: State) -> State:", "    # The blueprint requires a person to approve before the last step.",
                  '    answer = input(f"Approve? {state[\'request\']!r} [y/N] ")',
                  '    if answer.strip().lower() != "y":', '        raise SystemExit("not approved")',
                  '    return {"request": state["request"], "trace": state["trace"] + ["await_approval"]}', ""]
    order = [n for n, _, _ in steps]
    if gated:
        order = order[:-1] + ["await_approval", order[-1]]
    lines += ["", "graph = StateGraph(State)"]
    for name in order:
        lines.append(f"graph.add_node({name!r}, {name})".replace("'", '"'))
    lines.append(f'graph.set_entry_point("{order[0]}")')
    for a, b in zip(order, order[1:]):
        lines.append(f'graph.add_edge("{a}", "{b}")')
    lines += [f'graph.set_finish_point("{order[-1]}")', "app = graph.compile()", "", "",
              "def run(request: str) -> State:", '    return app.invoke({"request": request, "trace": []})', "",
              "", 'if __name__ == "__main__":', '    print(run("hello"))', ""]
    code = "\n".join(lines)
    ast.parse(code)
    readme = (f"# Starter for {_comment(blueprint.get('title', 'a blueprint'))}\n\nPinned to langgraph {entry['version']}; every graph-building LangGraph "
              "symbol used here (StateGraph and its add_node, add_edge, set_entry_point, set_finish_point and compile methods) was checked "
              "against Radar's API snapshot of that version. Running the graph (invoke) is not checked.\n\nThis skeleton does not express every topology's "
              "branching: it chains the blueprint's flow steps in order and adds an approval step where the blueprint has a human gate. "
              "Model calls are stubs. Run `pip install -r requirements.txt && python app.py`.\n")
    return {"framework": "langgraph", "version": entry["version"], "requirements": f"langgraph=={entry['version']}\n", "app": code,
            "readme": readme, "symbols": sorted(REQUIRED_SYMBOLS)}
