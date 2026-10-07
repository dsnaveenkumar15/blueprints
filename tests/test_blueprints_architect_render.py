# tests/test_blueprints_architect_render.py
from blueprints.architect import render
from blueprints.architect.capacity import capacity
from blueprints.cost import estimate, load_pricing
from blueprints.diagram import to_mermaid
from tests.blueprint_fixtures import good

NOW = 1_790_877_600.0


def result(**changes):
    bp = good()
    bp["title"] = "Refunds | fast"
    r = {"verdict": "passed", "reason": "Passed.", "brief": {"id": "x", "title": "T", "use_case": "u", "profile": bp["profile"]},
         "assumptions": ['How quickly must it answer: assumed "A person waits"'], "blueprint": bp,
         "record": {"context": "c", "decision": "d", "alternatives": [{"topology": "workflow", "summary": "s",
                                                                      "rejected_because": "2.0x the monthly cost"}],
                    "trade_offs": ["t"], "targets": {"latency_p95_s": 8, "availability_pct": 99.5, "quality": "q"},
                    "scaling": "grow out", "rollout": ["shadow", "canary"], "rollback": "back", "open_risks": [],
                    "runbook": [{"failure_mode": "wrong-route", "alert": "a", "meaning": "m", "first_action": "f"}]},
         "variant": {"blueprint": bp, "summary": "lean", "changes": ["fewer tokens"]},
         "variant_cost": estimate(bp, load_pricing(), NOW), "capacity": capacity(bp, load_pricing(), NOW),
         "cost": estimate(bp, load_pricing(), NOW), "diagram": to_mermaid(bp),
         "review": {"scores": {"requirement_fit": 5, "security": 4}, "findings": [], "summary": "ok", "passed": True},
         "redteam": [], "alternatives": [], "scores": [], "problems": [], "decision": {}, "description": "SECRET VISITOR TEXT"}
    r.update(changes)
    return r


def test_markdown_has_every_section_and_escapes_table_pipes():
    md = render.markdown(result())
    for heading in ("## Requirements", "## Design (router)", "## Decision record", "## Capacity", "## Runbook",
                    "## Review", "## If the budget is tighter", "```mermaid"):
        assert heading in md
    assert "Refunds \\| fast" in md and "2.0x the monthly cost" in md


def test_residual_risks_are_listed_in_the_download_and_never_hidden_by_a_pass():
    risk = {"component": "refund-gate", "scenario": "console bypass", "severity": "high", "control": "network allow-list", "controlled": False}
    res = result(residual=[risk], review={"scores": {"security": 4}, "findings": [], "attacks": [risk], "summary": "ok", "passed": True})
    md = render.markdown(res)
    assert "Residual risks" in md and "console bypass" in md and "network allow-list" in md and render.export(res)["residual"] == [risk]


def test_exports_never_carry_the_visitors_description():
    assert "SECRET VISITOR TEXT" not in render.markdown(result())
    assert "description" not in render.export(result())


def test_view_lists_scores_with_labels_and_prices():
    v = render.view(result())
    assert {"area": "Security", "score": 4} in v["scores"] and v["monthly"] == result()["cost"]["monthly_usd"]
