# tests/test_blueprints_architect_eval.py - the launch-gate harness explains a failing case; it never calls a model here.
from evals.architect_eval import CASES, why


def test_eight_fixed_cases_across_risk_levels():
    assert len(CASES) == 8 and len(set(CASES)) == 8


def test_a_failing_case_is_explained_with_scores_blockers_and_open_attacks():
    text = why({"brief": {"title": "Refund agent"},
                "review": {"scores": {"security": 3}, "findings": [{"blocking": True, "area": "security", "problem": "no auth"},
                                                                    {"blocking": False, "area": "rollout", "problem": "minor"}],
                           "residual": [{"severity": "high", "component": "kb", "scenario": "poisoned article"}]},
                "problems": [{"path": "flow", "problem": "too slow"}]})
    assert "Refund agent" in text and "blocking security: no auth" in text and "minor" not in text
    assert "open high attack on kb" in text and "check problem flow: too slow" in text


def test_a_result_without_a_review_still_explains_itself():
    assert "?" in why({}) and "scores None" in why({})
