"""Nearest published blueprints to a requirements profile. Pure arithmetic: the same profile always gives the same list."""
import math

from pydantic import ValidationError

from blueprints.models import Profile

ORDINALS = {"latency": ("batch", "near_real_time", "interactive"), "risk": ("low", "medium", "high"),
            "data_sensitivity": ("public", "internal", "pii", "regulated"),
            "autonomy": ("suggest", "act_with_approval", "act_autonomously")}
WEIGHTS = {"risk": 2.0, "autonomy": 2.0, "data_sensitivity": 1.0, "latency": 1.0}
FLAGS = {"open_ended": 2.0, "parallelizable": 1.0, "knowledge": 1.0, "quality_critical": 1.0}


def distance(a: dict, b: dict) -> tuple[float, list[str]]:
    total, same = 0.0, []
    for field, order in ORDINALS.items():
        gap = abs(order.index(a[field]) - order.index(b[field]))
        total += gap * WEIGHTS[field]
        if gap == 0:
            same.append(field)
    caps_a, caps_b = set(a["capabilities"]), set(b["capabilities"])
    union = caps_a | caps_b
    if union:
        total += 2.0 * (1 - len(caps_a & caps_b) / len(union))
    if caps_a == caps_b:
        same.append("capabilities")
    for flag, weight in FLAGS.items():
        if a[flag] != b[flag]:
            total += weight
    total += min(3.0, abs(math.log10(a["volume_per_day"]) - math.log10(b["volume_per_day"]))) * 0.5
    return total, same


def nearest(profile: dict, index: list[dict], limit: int = 3) -> list[dict]:
    scored = []
    for item in index:
        if not isinstance(item, dict) or not isinstance(item.get("profile"), dict):
            continue
        try:
            other = Profile.model_validate(item["profile"]).model_dump()
        except ValidationError:
            continue
        gap, same = distance(profile, other)
        scored.append((gap, str(item.get("id")), {"id": item.get("id"), "title": item.get("title"), "summary": item.get("summary"),
                                                  "topology": item.get("topology"), "risk": item.get("risk"),
                                                  "distance": round(gap, 2), "same": same}))
    scored.sort(key=lambda s: (s[0], s[1]))
    return [s[2] for s in scored[:limit]]
