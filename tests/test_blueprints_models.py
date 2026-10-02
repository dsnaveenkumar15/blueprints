import pytest
from pydantic import ValidationError

from blueprints.models import HIGH_RISK_CAPABILITIES, TOPOLOGIES, Blueprint
from blueprints.owasp import OWASP_LLM, OWASP_SOURCE, OWASP_VERSION
from blueprints.patterns import load_patterns, pattern_ids
from tests.blueprint_fixtures import good


def test_owasp_list_is_the_2025_top_ten():
    assert OWASP_VERSION == "2025" and OWASP_SOURCE.startswith("https://genai.owasp.org")
    assert list(OWASP_LLM) == [f"LLM{n:02d}" for n in range(1, 11)]
    assert OWASP_LLM["LLM01"] == "Prompt Injection" and OWASP_LLM["LLM06"] == "Excessive Agency"
    assert OWASP_LLM["LLM10"] == "Unbounded Consumption"


def test_constants():
    assert HIGH_RISK_CAPABILITIES == ("write", "exec", "network", "payment")
    assert TOPOLOGIES == ("workflow", "router", "orchestrator-workers", "supervisor", "evaluator-optimizer", "autonomous")


def test_the_good_fixture_validates_and_round_trips():
    bp = Blueprint.model_validate(good())
    assert bp.id == "refund-support" and bp.components[3].mitigations[0].by == "refund-gate"
    assert Blueprint.model_validate(bp.model_dump()).model_dump() == bp.model_dump()


@pytest.mark.parametrize("mutate", [
    lambda b: b.update(surprise=1),
    lambda b: b["profile"].update(surprise=1),
    lambda b: b["components"][0].update(id="Bad Id"),
    lambda b: b["components"][0].update(id="1starts-with-digit"),
    lambda b: b["components"][3].update(capabilities=["teleport"]),
    lambda b: b["profile"].update(risk="extreme"),
    lambda b: b["profile"].update(volume_per_day=0),
    lambda b: b["flow"][0].update(input_tokens=-1),
    lambda b: b["flow"][0].update(calls=0),
    lambda b: b.update(topology="swarm-of-doom"),
    lambda b: b.update(flow=[]),
    lambda b: b.update(eval_plan=[]),
    lambda b: b["threats"][0].update(owasp="OWASP-1"),
    lambda b: b["components"][3].update(tool_ref="not a repo"),
    lambda b: b["components"][3].update(framework_ref="cargo:thing"),
    lambda b: b.update(version=0),
    lambda b: b.update(components=b["components"] * 40),
], ids=lambda f: "case")
def test_schema_rejects_bad_shapes(mutate):
    bp = good()
    mutate(bp)
    with pytest.raises(ValidationError):
        Blueprint.model_validate(bp)


def test_pattern_catalog_has_the_eleven_spec_patterns_with_every_field():
    patterns = load_patterns()
    assert pattern_ids() == {"prompt-chaining", "routing", "parallelization", "orchestrator-workers", "evaluator-optimizer",
                             "react", "plan-and-execute", "reflection", "handoff", "hitl-approval", "rag"}
    assert len(patterns) == 11
    for p in patterns:
        assert {"id", "name", "when_to_use", "when_not_to_use", "failure_modes", "cost_profile", "status"} <= set(p)
        assert p["when_to_use"] and p["when_not_to_use"] and p["failure_modes"]
        assert p["status"] in ("draft", "reviewed")
        assert {"calls_multiplier", "latency_multiplier", "note"} <= set(p["cost_profile"])
        assert p["cost_profile"]["calls_multiplier"] >= 1.0 and p["cost_profile"]["latency_multiplier"] >= 1.0
