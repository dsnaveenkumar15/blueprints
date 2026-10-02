import copy

import pytest

from blueprints.linter import RULES, lint
from tests.blueprint_fixtures import context, good


def rules(result, severity=None):
    return {v["rule"] for v in result["violations"] if severity in (None, v["severity"])}


def test_the_good_blueprint_is_clean_with_and_without_context():
    for ctx in (None, context()):
        r = lint(good(), ctx)
        assert r == {"ok": True, "errors": 0, "warnings": 0, "violations": []}


def drop_gate(b, c):
    b["components"] = [x for x in b["components"] if x["id"] != "refund-gate"]
    b["components"][3]["mitigations"] = [{"capability": "write", "by": "audit"}]
    b["failure_modes"][0]["mitigation"] = "audit"
    b["threats"][1]["mitigation"] = "audit"


def autonomous_write(b, c):
    b["profile"].update(risk="medium", latency="batch", autonomy="act_autonomously", open_ended=True,
                        data_sensitivity="internal", task_kinds=1, knowledge=False)
    b["topology"] = "autonomous"
    drop_gate(b, c)


MUTATIONS = [
    ("extra field", lambda b, c: b.update(surprise=1), "schema"),
    ("bad capability", lambda b, c: b["components"][3].update(capabilities=["teleport"]), "schema"),
    ("duplicate id", lambda b, c: b["guardrails"][0].update(id="kb"), "duplicate-id"),
    ("guardrail protects a ghost", lambda b, c: b["guardrails"][0].update(protects=["ghost"]), "dangling-ref"),
    ("failure mode on a ghost", lambda b, c: b["failure_modes"][0].update(component="ghost"), "dangling-ref"),
    ("failure mode mitigated by a ghost", lambda b, c: b["failure_modes"][0].update(mitigation="ghost"), "dangling-ref"),
    ("threat on a ghost", lambda b, c: b["threats"][0].update(component="ghost"), "dangling-ref"),
    ("flow step on a ghost", lambda b, c: b["flow"][0].update(component="ghost"), "dangling-ref"),
    ("mitigation by a ghost", lambda b, c: b["components"][3]["mitigations"][0].update(by="ghost"), "dangling-ref"),
    ("flow step on a non-model", lambda b, c: b["flow"][0].update(component="kb"), "step-not-a-model"),
    ("write without mitigation", lambda b, c: b["components"][3].update(mitigations=[]), "unmitigated-capability"),
    ("mitigation by a model", lambda b, c: b["components"][3]["mitigations"][0].update(by="answer-model"),
     "mitigation-not-a-control"),
    ("unknown owasp id", lambda b, c: b["threats"][0].update(owasp="LLM99"), "unknown-owasp"),
    ("unknown pattern", lambda b, c: b.update(patterns=["routing", "magic"]), "unknown-pattern"),
    ("missing mandatory gate", drop_gate, "missing-mandatory"),
    ("missing mandatory input filter", lambda b, c: b["guardrails"].pop(0), "missing-mandatory"),
    ("forbidden topology", lambda b, c: b.update(topology="autonomous"), "forbidden-topology"),
    ("autonomous write without a gate", autonomous_write, "autonomous-write-without-gate"),
    ("model missing", lambda b, c: b["components"][0].update(model=None), "model-missing"),
    ("unknown model", lambda b, c: b["components"][0].update(model="gpt-imaginary"), "unknown-model"),
    ("tool graded C", lambda b, c: c["trust_grades"].update({"acme/orders-mcp": "C"}), "tool-not-eligible"),
    ("tool without a grade", lambda b, c: c["trust_grades"].clear(), "tool-not-eligible"),
    ("untracked framework", lambda b, c: b["components"][3].update(framework_ref="pypi:untracked"), "unknown-framework"),
]


@pytest.mark.parametrize("name, mutate, rule", MUTATIONS, ids=[m[0] for m in MUTATIONS])
def test_every_bad_mutation_is_flagged_with_its_rule(name, mutate, rule):
    bp, ctx = good(), context()
    mutate(bp, ctx)
    result = lint(bp, ctx)
    assert result["ok"] is False and rule in rules(result, "error"), result["violations"]


def test_warnings_do_not_block():
    bp = good()
    bp["topology"] = "workflow"                              # allowed but not what the engine would pick
    bp["guardrails"].append({"id": "idle", "kind": "rate_limit", "protects": [], "description": "protects nothing"})
    bp["components"].append({"id": "spare-model", "role": "unused", "kind": "model", "name": "Spare",
                             "model": "claude-haiku-4-5", "capabilities": [], "mitigations": []})
    bp["threats"] = [t for t in bp["threats"] if t["owasp"] != "LLM06"]
    r = lint(bp, context())
    assert r["ok"] is True and r["errors"] == 0
    assert {"topology-differs", "guardrail-protects-nothing", "unused-model", "excessive-agency-unmapped"} <= rules(r, "warning")


def test_context_keys_that_are_missing_skip_their_rules():
    bp = good()
    bp["components"][0]["model"] = "gpt-imaginary"
    bp["components"][3]["framework_ref"] = "pypi:untracked"
    assert lint(bp, {"trust_grades": None})["ok"] is True
    assert lint(bp, {})["ok"] is True


@pytest.mark.parametrize("junk", [None, [], "text", 42, {}, {"id": "x"}, [1, 2, 3], {"components": "nope"}])
def test_odd_inputs_return_schema_violations_instead_of_raising(junk):
    r = lint(junk, context())
    assert r["ok"] is False and rules(r) == {"schema"}


def test_hostile_sizes_and_depth_do_not_crash_or_hang():
    bp = good()
    bp["components"] = bp["components"] * 2000
    bp["summary"] = "x" * 5_000_000
    assert lint(bp)["ok"] is False
    deep = {"a": None}
    node = deep
    for _ in range(5000):
        node["a"] = {"a": None}
        node = node["a"]
    assert lint(deep)["ok"] is False
    assert lint({**good(), "profile": deep})["ok"] is False


def test_violations_have_the_documented_shape_and_every_rule_is_documented():
    bp = good()
    bp["threats"][0]["owasp"] = "LLM99"
    r = lint(bp)
    v = r["violations"][0]
    assert set(v) == {"rule", "severity", "path", "message"} and v["severity"] in ("error", "warning")
    assert r["errors"] == sum(1 for x in r["violations"] if x["severity"] == "error")
    for mutate in (m[1] for m in MUTATIONS):
        b, c = good(), context()
        mutate(b, c)
        assert rules(lint(b, c)) <= set(RULES)


def test_lint_does_not_mutate_its_input():
    bp = good()
    before = copy.deepcopy(bp)
    lint(bp, context())
    assert bp == before


def test_tool_refs_are_matched_case_insensitively():
    bp, ctx = good(), context()
    bp["components"][3]["tool_ref"] = "Acme/Orders-MCP"
    assert lint(bp, ctx)["ok"] is True


def test_a_gate_that_is_not_wired_to_the_acting_capability_is_an_error():
    bp = good()
    bp["components"][3]["mitigations"] = [{"capability": "write", "by": "audit"}]        # the gate exists but guards nothing
    assert "gate-not-wired" in rules(lint(bp, context()), "error")


def test_a_profile_capability_that_no_component_declares_has_no_control():
    bp = good()
    bp["components"][3]["capabilities"] = ["read"]
    bp["components"][3]["mitigations"] = []
    assert "capability-undeclared" in rules(lint(bp, context()), "error")


def test_a_mandatory_guardrail_must_protect_something():
    bp = good()
    bp["guardrails"][0]["protects"] = []
    assert "mandatory-guardrail-idle" in rules(lint(bp, context()), "error")


def test_a_tool_without_a_reference_is_flagged_when_grades_are_known_and_never_blocks():
    bp = good()
    bp["components"][3].pop("tool_ref")
    r = lint(bp, context())
    assert "tool-without-reference" in rules(r, "warning") and r["ok"] is True
    assert "tool-without-reference" not in rules(lint(bp, {})) and "tool-without-reference" not in rules(lint(bp, {"models": None}))


@pytest.mark.parametrize("text", ["It costs $0.50 per call.", "Built on langgraph 1.2.3.", "Prices as of 2026.", "Needs grade B tools.",
                                  "About US$ 20 a month.", "Tested on 3.12.4."])
def test_volatile_facts_in_free_text_are_flagged_as_warnings(text):
    for where in ("summary", "role", "description", "note", "notes"):
        bp = good()
        if where == "summary":
            bp["summary"] = text
        elif where == "role":
            bp["components"][0]["role"] = text
        elif where == "description":
            bp["guardrails"][0]["description"] = text
        elif where == "note":
            bp["threats"][0]["note"] = text
        else:
            bp["deployment"]["notes"] = text
        r = lint(bp, context())
        assert "volatile-fact-in-text" in rules(r, "warning") and r["ok"] is True, (where, text)


def test_ordinary_numbers_in_text_are_not_volatile_facts():
    bp = good()
    bp["summary"] = "Handles 5000 requests a day across 3 kinds of request with a 0.95 routing threshold in 2 steps."
    assert "volatile-fact-in-text" not in rules(lint(bp, context()))
