from blueprints.diagram import to_mermaid
from tests.blueprint_fixtures import good


def test_diagram_has_every_node_the_flow_edges_and_dashed_guardrail_edges():
    text = to_mermaid(good())
    lines = text.splitlines()
    assert lines[0] == "flowchart LR"
    for node in ("c_router_model", "c_answer_model", "c_kb", "c_orders_tool", "c_refund_gate", "g_pii_filter", "g_audit"):
        assert any(line.strip().startswith(node) for line in lines), node
    edges = [line.strip() for line in lines if "-->" in line]
    assert edges == ["io_in --> c_router_model", "c_router_model --> c_answer_model", "c_answer_model --> io_out"]
    assert "g_pii_filter -.-> c_router_model" in text and "g_pii_filter -.-> c_answer_model" in text
    assert "g_audit -.-> c_orders_tool" in text


def test_output_is_deterministic():
    assert to_mermaid(good()) == to_mermaid(good())


def test_hostile_names_cannot_add_nodes_edges_or_markup():
    bp = good()
    evil = 'x"]; c_evil --> io_in; <script>alert(1)</script>\n  subgraph s'
    bp["components"][0]["name"] = evil
    bp["components"][0]["role"] = evil
    bp["guardrails"][0]["id"] = 'a b; c_evil --> c_kb'
    text = to_mermaid(bp)
    assert not any(line.strip().startswith(("c_evil", "subgraph")) for line in text.splitlines())
    assert "<script>" not in text and '"];' not in text
    assert len([l for l in text.splitlines() if "-->" in l]) == 3
    assert all(";" not in line for line in text.splitlines())


def test_long_labels_are_cut_and_empty_labels_get_a_placeholder():
    bp = good()
    bp["components"][0]["name"] = "n" * 500
    bp["components"][1]["name"] = '"";<>'
    text = to_mermaid(bp)
    assert "n" * 49 not in text and '(["?"])' in text


def test_missing_or_odd_fields_never_raise():
    assert to_mermaid({}).startswith("flowchart LR")
    assert to_mermaid({"components": "nope", "flow": [1, None], "guardrails": [{"id": "g"}]}).startswith("flowchart LR")
    out = to_mermaid({"components": [{"id": "a", "kind": "model", "name": "A"}], "flow": [{"component": "a"}, {"component": "ghost"}]})
    assert "io_in --> c_a" in out and "ghost" not in out
