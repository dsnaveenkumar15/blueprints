# blueprints/architect/capacity.py
"""Capacity model (spec §3 step 6). Pure arithmetic over the blueprint and the dated price table: every capacity,
latency and cost number a visitor sees comes from here, never from a model."""
from blueprints.cost import estimate

PEAK_FACTOR = 3.0                      # peak hour runs at three times the daily average
LATENCY_TARGET_S = {"interactive": 5.0, "near_real_time": 30.0, "batch": 900.0}
SCALES = (1, 10, 100)


def _first_limit(base: dict, target: float, scales: list[dict], budget: int) -> dict:
    if not base["complete"]:
        return {"kind": "unpriced", "text": "Cost cannot be computed: a step's model has no price in the table."}
    timed = [s for s in base["steps"] if s["latency_s"] is not None]
    if base["latency_s"] > target and timed:
        slowest = max(timed, key=lambda s: s["latency_s"])
        return {"kind": "latency", "text": f"Latency: the flow takes {base['latency_s']:g} s against a {target:g} s target; "
                                           f"step {slowest['id']} is the largest share ({slowest['latency_s']:g} s)."}
    crossing = next((s for s in scales if s["over_budget"]), None)
    if crossing:
        return {"kind": "budget", "text": f"Budget: at {crossing['scale']}x volume the monthly cost "
                                          f"${crossing['monthly_usd']:,.0f} exceeds the ${budget:,} budget."}
    priced = [s for s in base["steps"] if s["usd"] is not None]
    top = max(priced, key=lambda s: s["usd"])
    share = top["usd"] / base["per_request_usd"] if base["per_request_usd"] else 0
    return {"kind": "cost", "text": f"Cost grows in step with volume; step {top['id']} is {share:.0%} of the cost per request."}


def capacity(blueprint: dict, pricing: dict, now: float) -> dict:
    profile = blueprint["profile"]
    volume = profile["volume_per_day"]
    base = estimate(blueprint, pricing, now)
    target = LATENCY_TARGET_S[profile["latency"]]
    peak_rps = volume / 86400 * PEAK_FACTOR
    steps = [{"id": s["id"], "latency_s": s["latency_s"], "usd": s["usd"],
              "share_of_target": round(s["latency_s"] / target, 3) if s["latency_s"] is not None else None}
             for s in base["steps"]]
    scales = []
    for k in SCALES:
        e = estimate(blueprint, pricing, now, volume * k)
        scales.append({"scale": k, "volume_per_day": volume * k, "peak_rps": round(peak_rps * k, 3),
                       "concurrency": round(peak_rps * k * base["latency_s"], 2), "monthly_usd": e["monthly_usd"],
                       "over_budget": e["over_budget"]})
    return {"peak_factor": PEAK_FACTOR, "latency_target_s": target, "latency_s": base["latency_s"],
            "within_target": base["latency_s"] <= target, "per_request_usd": base["per_request_usd"],
            "complete": base["complete"], "steps": steps, "scales": scales,
            "first_limit": _first_limit(base, target, scales, profile["budget_usd_month"])}
