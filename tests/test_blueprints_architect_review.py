# tests/test_blueprints_architect_review.py
import json

import pytest

pytest.importorskip("langchain_core")

from blueprints.architect import review  # noqa: E402
from tests.blueprint_fixtures import good, reply, routed_gateway  # noqa: E402
from tests.test_blueprints_architect_record import a_record  # noqa: E402

SCORES = {a: 4 for a in review.AREAS}


def reviewed(body):
    return review.run(routed_gateway({"review": [reply(body)]}), good(), a_record(), {}, [])


def test_review_passes_only_with_every_area_at_four_and_no_blocking_finding():
    assert reviewed({**SCORES, "findings": [], "summary": "solid"})["passed"]
    assert not reviewed({**SCORES, "security": 3, "findings": [], "summary": "s"})["passed"]
    blocked = reviewed({**SCORES, "summary": "s", "findings": [{"area": "security", "blocking": True, "problem": "no auth", "fix": "add auth"}]})
    assert not blocked["passed"] and review.problems(blocked) == [{"path": "security", "problem": "no auth", "fix": "add auth"}]


def test_a_low_area_without_a_finding_still_gives_the_fix_step_something_to_do():
    result = reviewed({**SCORES, "rollout": 2, "findings": [], "summary": "s"})
    assert review.problems(result) == [{"path": "rollout", "problem": "rollout scored 2 of 5", "fix": "raise it to production-ready"}]


def test_the_reviewer_receives_only_the_design_never_the_visitors_words():
    gw = routed_gateway({"review": [reply({**SCORES, "findings": [], "summary": "s"})]})
    review.run(gw, good(), a_record(), {}, ["How quickly must it answer: assumed \"x\""])
    sent = gw.model.calls[0][1].content
    payload = json.loads(sent.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert set(payload) == {"assumptions", "blueprint", "capacity", "record"}
