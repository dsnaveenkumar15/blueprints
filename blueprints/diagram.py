"""Mermaid text from blueprint JSON, built by code (spec §7.7). Every label and id is reduced to a safe alphabet,
so blueprint text can never add nodes, edges or markup."""
import re

_UNSAFE_LABEL = re.compile(r"[^A-Za-z0-9 .,:/@+_()-]")
_UNSAFE_ID = re.compile(r"[^a-z0-9]")
_SHAPES = {"model": ('(["', '"])'), "tool": ('[["', '"]]'), "memory": ('[("', '")]'),
           "retrieval": ('[("', '")]'), "human_gate": ('{{"', '"}}')}
LABEL_MAX = 48


def _label(text) -> str:
    return _UNSAFE_LABEL.sub("", str(text))[:LABEL_MAX].strip() or "?"


def _node(prefix: str, raw) -> str:
    return prefix + _UNSAFE_ID.sub("_", str(raw).lower())


def to_mermaid(blueprint: dict) -> str:
    comps = [c for c in blueprint.get("components") or [] if isinstance(c, dict) and c.get("id") is not None] \
        if isinstance(blueprint.get("components"), list) else []
    guards = [g for g in blueprint.get("guardrails") or [] if isinstance(g, dict) and g.get("id") is not None] \
        if isinstance(blueprint.get("guardrails"), list) else []
    ids = {str(c["id"]): _node("c_", c["id"]) for c in comps}
    lines = ["flowchart LR", '    io_in(["request"])', '    io_out(["response"])']
    for c in comps:
        left, right = _SHAPES.get(c.get("kind"), ('["', '"]'))
        lines.append(f"    {ids[str(c['id'])]}{left}{_label(c.get('name'))}{right}")
    for g in guards:
        lines.append(f"    {_node('g_', g['id'])}>\"{_label(g.get('kind'))}\"]")
    steps = [s for s in blueprint.get("flow") or [] if isinstance(s, dict) and str(s.get("component")) in ids] \
        if isinstance(blueprint.get("flow"), list) else []
    chain = ["io_in"]
    for s in steps:
        node = ids[str(s["component"])]
        if node != chain[-1]:
            chain.append(node)
    chain.append("io_out")
    if len(chain) > 2:
        lines += [f"    {a} --> {b}" for a, b in zip(chain, chain[1:])]
    for g in guards:
        protects = g.get("protects") if isinstance(g.get("protects"), list) else []
        lines += [f"    {_node('g_', g['id'])} -.-> {ids[str(p)]}" for p in protects if str(p) in ids]
    return "\n".join(lines) + "\n"
