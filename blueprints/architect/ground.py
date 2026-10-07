# blueprints/architect/ground.py
"""Grounding (spec §3 step 3): what the model designs from instead of memory. Pure code over published data: the two
nearest published blueprints, the matching patterns, and the allowed models, tools and frameworks."""
from blueprints.finder import nearest
from blueprints.generate import constraints


def ground(profile: dict, decision: dict, index_rows: list[dict], entry_for, patterns: list[dict], context: dict,
           pricing: dict) -> dict:
    references = []
    for n in nearest(profile, index_rows, limit=2):
        entry = entry_for(n["id"]) or {}
        if isinstance(entry.get("body"), dict):
            references.append({"id": n["id"], "title": n["title"], "topology": n["topology"], "distance": n["distance"],
                               "blueprint": entry["body"]})
    return {"references": references, "patterns": [p for p in patterns if p.get("id") in decision["patterns"]],
            "limits": constraints(context, pricing)}
