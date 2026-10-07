# tests/test_blueprints_architect_clarify.py
import pytest

pytest.importorskip("langchain_core")

from blueprints.architect import clarify  # noqa: E402
from tests.blueprint_fixtures import reply, scripted_gateway  # noqa: E402

ALL = list(clarify.DEFAULTS)


def test_stated_valid_values_are_kept_and_invalid_or_unstated_ones_fall_back():
    profile, known = clarify.merge({"risk": "high", "latency": "warp-speed", "volume_per_day": 50},
                                   ["risk", "latency"])
    assert profile["risk"] == "high" and profile["latency"] == clarify.DEFAULTS["latency"]
    assert profile["volume_per_day"] == clarify.DEFAULTS["volume_per_day"] and known == ["risk"]


def test_nothing_is_asked_when_everything_is_known():
    assert clarify.questions(dict(clarify.DEFAULTS), ALL) == []


def test_a_field_that_changes_nothing_is_assumed_not_asked():
    known = [f for f in ALL if f != "parallelizable"]
    assert clarify.questions(dict(clarify.DEFAULTS), known) == []


def test_risk_is_asked_when_a_write_capability_is_still_possible():
    known = [f for f in ALL if f not in ("risk", "capabilities")]
    fields = [q["field"] for q in clarify.questions(dict(clarify.DEFAULTS), known)]
    assert set(fields) == {"risk", "capabilities"}


def test_budget_and_volume_are_asked_because_they_price_the_design():
    known = [f for f in ALL if f != "budget_usd_month"]
    assert [q["field"] for q in clarify.questions(dict(clarify.DEFAULTS), known)] == ["budget_usd_month"]


def test_at_most_four_questions_with_the_assumed_value_suggested():
    qs = clarify.questions(dict(clarify.DEFAULTS), [])
    assert 1 <= len(qs) <= 4
    for q in qs:
        assert clarify.OPTIONS[q["field"]][q["suggested"]][0] == clarify.DEFAULTS[q["field"]]


def test_answers_apply_and_skipped_fields_become_assumptions():
    asked = clarify.questions(dict(clarify.DEFAULTS), [])
    first = asked[0]["field"]
    profile, assumptions = clarify.apply_answers(dict(clarify.DEFAULTS), [], asked, {first: len(clarify.OPTIONS[first]) - 1})
    assert profile[first] == clarify.OPTIONS[first][-1][0]
    assert not any(a.startswith(clarify.TEXT[first].rstrip("?")) for a in assumptions) and len(assumptions) == len(ALL) - 1


@pytest.mark.parametrize("answers", [{"not_a_field": 0}, {"risk": 99}, {"risk": "0"}])
def test_answers_outside_the_offered_options_are_refused(answers):
    asked = [{"field": "risk"}]
    with pytest.raises(ValueError):
        clarify.apply_answers(dict(clarify.DEFAULTS), [], asked, answers)


def test_clarify_reads_the_description_through_the_model():
    gw = scripted_gateway({"free": [reply({"title": "Refund helper", "use_case": "customer support",
                                           "profile": {"capabilities": ["read", "write"], "risk": "high"},
                                           "stated": ["capabilities", "risk"]})]})
    out = clarify.clarify(gw, "An agent that refunds orders.")
    assert out["profile"]["capabilities"] == ["read", "write"] and out["known"] == ["risk", "capabilities"]
    assert all(q["field"] not in ("risk", "capabilities") for q in out["questions"])
