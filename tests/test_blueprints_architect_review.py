# tests/test_blueprints_architect_review.py
import json

import pytest

pytest.importorskip("langchain_core")

from blueprints.architect import fix, redteam, review  # noqa: E402
from tests.blueprint_fixtures import good, reply, scripted_gateway  # noqa: E402
from tests.test_blueprints_architect_record import a_record  # noqa: E402

SCORES = {a: 4 for a in review.AREAS}


def test_red_team_findings_of_medium_or_above_become_problems():
    gw = scripted_gateway({"free": [reply({"findings": [
        {"component": "orders-tool", "scenario": "refund loop", "severity": "high", "control": "cap refunds per hour"},
        {"component": "kb", "scenario": "typo", "severity": "low", "control": "none"}]})]})
    findings = redteam.run(gw, good())
    assert [p["path"] for p in redteam.problems(findings)] == ["components.orders-tool"]


def test_review_passes_only_with_every_area_at_four_and_no_blocking_finding():
    ok = scripted_gateway({"strong": [reply({**SCORES, "findings": [], "summary": "solid"})]})
    assert review.run(ok, good(), a_record(), {}, [], [])["passed"]
    low = scripted_gateway({"strong": [reply({**SCORES, "security": 3, "findings": [], "summary": "s"})]})
    assert not review.run(low, good(), a_record(), {}, [], [])["passed"]
    blocked = scripted_gateway({"strong": [reply({**SCORES, "summary": "s", "findings": [
        {"area": "security", "blocking": True, "problem": "no auth", "fix": "add auth"}]})]})
    result = review.run(blocked, good(), a_record(), {}, [], [])
    assert not result["passed"] and review.problems(result) == [{"path": "security", "problem": "no auth", "fix": "add auth"}]


def test_the_reviewer_receives_only_the_design_never_the_visitors_words():
    gw = scripted_gateway({"strong": [reply({**SCORES, "findings": [], "summary": "s"})]})
    review.run(gw, good(), a_record(), {}, [], ["How quickly must it answer: assumed \"x\""])
    sent = gw.factory("strong").calls[0][1].content
    payload = json.loads(sent.split("\n", 1)[1].rsplit("\n", 1)[0])
    assert set(payload) == {"assumptions", "blueprint", "capacity", "record", "redteam"}


def test_fix_returns_a_pinned_blueprint_and_a_valid_record():
    moved = {**good(), "id": "changed", "topology": "autonomous"}
    gw = scripted_gateway({"free": [reply({"blueprint": moved, "record": a_record()})]})
    brief = {"id": "refund-support", "title": "Refund support agent", "use_case": "customer support", "profile": good()["profile"]}
    body, rec, var = fix.run(gw, good(), a_record(), None, [{"path": "x", "problem": "p", "fix": "f"}], brief, "router")
    assert body["id"] == "refund-support" and body["topology"] == "router" and rec["runbook"] and var is None
