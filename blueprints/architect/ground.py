# blueprints/architect/ground.py
"""Grounding (spec §3 step 3): what the model designs from instead of memory. Pure code over published data: the two
nearest published blueprints, the matching patterns, and the allowed models, tools and frameworks."""
from blueprints.architect.capacity import LATENCY_TARGET_S
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
    limits = constraints(context, pricing)
    limits["latency_target_s"] = LATENCY_TARGET_S[profile["latency"]]
    limits["model_speeds"] = {m: {"ttft_s": pricing["models"][m]["ttft_s"], "tokens_per_s": pricing["models"][m]["tokens_per_s"]}
                              for m in limits["allowed_models"]}
    return {"references": references, "patterns": [p for p in patterns if p.get("id") in decision["patterns"]],
            "limits": limits}
