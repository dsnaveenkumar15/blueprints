import pytest

from evals.blueprints_eval import BAD_CASES, GOLDEN, MIN_PRECISION, MIN_RECALL, evaluate


def test_gates_hold():
    result = evaluate()
    assert result["recall"] >= MIN_RECALL and result["precision"] >= MIN_PRECISION and result["decision_accuracy"] == 1.0, result


def test_the_labelled_sets_are_substantial():
    assert len(GOLDEN) >= 8 and len(BAD_CASES) >= 12


@pytest.mark.parametrize("name", [c[0] for c in BAD_CASES])
def test_every_known_bad_blueprint_is_flagged_with_its_rule(name):
    assert evaluate()["bad"][name] is True


def test_every_golden_profile_matches():
    assert all(evaluate()["golden"].values())


def test_the_eval_covers_the_living_link_and_autonomy_rules():
    from evals.blueprints_eval import BAD_CASES, evaluate
    covered = {case[3] for case in BAD_CASES}
    assert {"tool-not-eligible", "unknown-framework", "autonomous-write-without-gate"} <= covered
    result = evaluate()
    for name in ("tool graded below B", "tool with no Trust grade", "framework Radar does not track", "autonomous write without an approval gate"):
        assert result["bad"][name] is True, name
