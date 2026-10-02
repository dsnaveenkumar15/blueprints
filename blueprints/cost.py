"""Deterministic cost and latency estimate (spec §7.5): tokens per step x dated, sourced prices x volume.
The model never produces a number; an unpriced model makes the estimate incomplete instead of guessing."""
import json
from datetime import datetime, timezone
from pathlib import Path

PRICING = Path(__file__).with_name("pricing.json")
DAYS_PER_MONTH = 30
_PRICE_FIELDS = ("input_per_mtok", "output_per_mtok", "ttft_s", "tokens_per_s")
ASSUMPTIONS = [
    "token counts per step are estimates written with the blueprint and approved by the owner, not measurements",
    "steps run one after another, so parallel branches make the latency an overestimate",
    "latency uses an assumed time to first token and output speed per model",
    f"{DAYS_PER_MONTH} days per month and the stated requests per day",
]


def load_pricing(path=None) -> dict:
    try:
        data = json.loads(Path(path or PRICING).read_text(encoding="utf-8"))
        if not isinstance(data, dict) or not data.get("source") or not isinstance(data.get("models"), dict) \
                or not data["models"]:
            raise ValueError("pricing needs a source and a non-empty models table")
        datetime.fromisoformat(data["as_of"])
        if not isinstance(data.get("max_age_days"), int):
            raise ValueError("max_age_days must be an integer")
        for name, model in data["models"].items():
            if not all(isinstance(model.get(f), (int, float)) and not isinstance(model.get(f), bool) and model[f] >= 0
                       for f in _PRICE_FIELDS):
                raise ValueError(f"model {name!r} needs numeric {', '.join(_PRICE_FIELDS)}")
    except (OSError, KeyError, TypeError, AttributeError, json.JSONDecodeError) as exc:
        raise ValueError(f"malformed pricing file: {exc}") from exc
    return data


def is_stale(pricing: dict, now: float) -> bool:
    as_of = datetime.fromisoformat(pricing["as_of"]).replace(tzinfo=timezone.utc).timestamp()
    return now - as_of > pricing["max_age_days"] * 86400


def estimate(blueprint: dict, pricing: dict, now: float, volume_per_day: int | None = None) -> dict:
    comps = {c.get("id"): c for c in blueprint.get("components", []) if isinstance(c, dict)}
    profile = blueprint.get("profile") or {}
    volume = max(1, int(volume_per_day if volume_per_day is not None else profile.get("volume_per_day", 1)))
    steps, unpriced = [], []
    per_request = latency = 0.0
    for step in blueprint.get("flow", []):
        model_id = (comps.get(step.get("component")) or {}).get("model")
        price = pricing["models"].get(model_id)
        if price is None:
            unpriced.append(step.get("id"))
            steps.append({"id": step.get("id"), "model": model_id, "calls": step.get("calls", 1), "usd": None, "latency_s": None,
                          "inputs": None})
            continue
        calls = float(step.get("calls", 1))
        usd = (step["input_tokens"] * price["input_per_mtok"] + step["output_tokens"] * price["output_per_mtok"]) / 1e6 * calls
        seconds = (price["ttft_s"] + step["output_tokens"] / price["tokens_per_s"]) * calls
        per_request += usd
        latency += seconds
        steps.append({"id": step["id"], "model": model_id, "calls": calls, "usd": round(usd, 6), "latency_s": round(seconds, 2),
                      "inputs": {"input_tokens": step["input_tokens"], "output_tokens": step["output_tokens"], "calls": calls,
                                 "input_per_mtok": price["input_per_mtok"], "output_per_mtok": price["output_per_mtok"],
                                 "ttft_s": price["ttft_s"], "tokens_per_s": price["tokens_per_s"]}})
    complete = not unpriced
    monthly = round(per_request * volume * DAYS_PER_MONTH, 2) if complete else None
    budget = profile.get("budget_usd_month", 0)
    return {"per_request_usd": round(per_request, 6), "monthly_usd": monthly, "latency_s": round(latency, 2),
            "volume_per_day": volume, "budget_usd_month": budget,
            "over_budget": bool(complete and budget and monthly > budget), "steps": steps, "complete": complete,
            "unpriced": unpriced,
            "pricing": {"as_of": pricing["as_of"], "source": pricing["source"], "stale": is_stale(pricing, now),
                        "latency_status": pricing.get("latency_status", "assumed")},
            "assumptions": list(ASSUMPTIONS)}
