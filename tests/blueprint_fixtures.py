"""A known-good blueprint used across the Blueprints tests. Each call returns a fresh deep copy."""
import copy

GOOD = {
    "id": "refund-support",
    "title": "Refund support agent",
    "summary": "Answers order questions from a knowledge base and issues refunds only after a person approves.",
    "use_case": "customer support",
    "version": 1,
    "changelog": ["v1: initial"],
    "profile": {"latency": "interactive", "volume_per_day": 5000, "risk": "high", "data_sensitivity": "pii",
                "autonomy": "act_with_approval", "budget_usd_month": 2000, "capabilities": ["read", "write"],
                "open_ended": False, "task_kinds": 3, "parallelizable": False, "knowledge": True,
                "quality_critical": False},
    "topology": "router",
    "patterns": ["routing", "rag", "hitl-approval"],
    "components": [
        {"id": "router-model", "role": "classify the request", "kind": "model", "name": "Router", "model": "claude-haiku-4-5",
         "capabilities": [], "mitigations": []},
        {"id": "answer-model", "role": "write the answer", "kind": "model", "name": "Answerer", "model": "claude-sonnet-5-5",
         "capabilities": [], "mitigations": []},
        {"id": "kb", "role": "policy and order-help articles", "kind": "retrieval", "name": "Knowledge base index",
         "capabilities": ["read"], "mitigations": []},
        {"id": "orders-tool", "role": "look up and refund orders", "kind": "tool", "name": "Orders API",
         "tool_ref": "acme/orders-mcp", "capabilities": ["read", "write"],
         "mitigations": [{"capability": "write", "by": "refund-gate"}]},
        {"id": "refund-gate", "role": "a person approves every refund", "kind": "human_gate", "name": "Refund approval",
         "capabilities": [], "mitigations": []},
    ],
    "guardrails": [
        {"id": "pii-filter", "kind": "input_filter", "protects": ["router-model", "answer-model"],
         "description": "Redacts card numbers and national ids before any model sees the text."},
        {"id": "audit", "kind": "audit_log", "protects": ["orders-tool"],
         "description": "Every tool call is logged with the request id and the approver."},
    ],
    "flow": [
        {"id": "classify", "component": "router-model", "input_tokens": 600, "output_tokens": 50, "calls": 1},
        {"id": "answer", "component": "answer-model", "input_tokens": 2500, "output_tokens": 400, "calls": 1},
    ],
    "failure_modes": [
        {"id": "wrong-route", "description": "A refund request is routed to the plain-answer path.",
         "component": "router-model", "mitigation": "refund-gate"},
        {"id": "stale-policy", "description": "An answer quotes a policy that has changed.",
         "component": "kb", "mitigation": "audit"},
    ],
    "threats": [
        {"owasp": "LLM01", "component": "answer-model", "mitigation": "pii-filter", "note": "untrusted customer text"},
        {"owasp": "LLM06", "component": "orders-tool", "mitigation": "refund-gate", "note": "write access"},
    ],
    "eval_plan": [{"metric": "routing accuracy", "method": "labelled set of 200 tickets", "threshold": ">= 0.95"}],
    "observability": ["traces per request", "cost per request", "approval latency"],
    "deployment": {"runtime": "container behind the support portal", "notes": ""},
}

CONTEXT = {"trust_grades": {"acme/orders-mcp": "B"}, "frameworks": {"pypi:langgraph"},
           "models": {"claude-haiku-4-5", "claude-sonnet-5-5", "claude-opus-5-5", "claude-fable-5-1"}}


def good() -> dict:
    return copy.deepcopy(GOOD)


def context() -> dict:
    return {"trust_grades": dict(CONTEXT["trust_grades"]), "frameworks": set(CONTEXT["frameworks"]),
            "models": set(CONTEXT["models"])}
