# blueprints/architect/llm.py
"""Structured model steps: the reply is one JSON object, validated against a pydantic schema, with one repair retry.
JSON in the reply rather than tool calls, because free models' tool support varies by provider."""
import json
import re

from langchain_core.messages import HumanMessage, SystemMessage
from pydantic import BaseModel, ValidationError

FENCE = re.compile(r"^```(?:json)?\s*|\s*```$")


class StepFailed(Exception):
    """A model step returned invalid output twice. The message never contains the model's text."""


def untrusted(payload: dict) -> str:
    body = json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True).replace("</", "<\\/")
    return f"<untrusted_brief>\n{body}\n</untrusted_brief>"


def _text(content) -> str:
    if isinstance(content, str):
        return content
    return "".join(part.get("text", "") if isinstance(part, dict) else str(part) for part in content or [])


def parse(text: str, schema: type[BaseModel]) -> BaseModel:
    raw = FENCE.sub("", (text or "").strip())
    start, end = raw.find("{"), raw.rfind("}")
    return schema.model_validate(json.loads(raw[start:end + 1] if 0 <= start < end else raw))


def _why(exc: Exception) -> str:
    if isinstance(exc, ValidationError):
        e = exc.errors()[0]
        return f"{'.'.join(str(p) for p in e['loc']) or 'root'}: {e['msg']}"[:300]
    return "the reply was not one JSON object"


def structured(gateway, role: str, system: str, payload: dict, schema: type[BaseModel]) -> BaseModel:
    """BudgetExceeded and gateway RuntimeErrors propagate; invalid output twice raises StepFailed."""
    messages = [SystemMessage(f"{system} Reply with one JSON object only, no prose, matching this JSON schema: "
                              f"{json.dumps(schema.model_json_schema(), separators=(',', ':'))}"),
                HumanMessage(untrusted(payload))]
    for attempt in (1, 2):
        msg = gateway.invoke(messages, role=role)
        try:
            return parse(_text(msg.content), schema)
        except (ValueError, ValidationError) as exc:
            if attempt == 2:
                raise StepFailed(f"{schema.__name__}: the model returned invalid output twice") from None
            messages += [msg, HumanMessage(f"That reply was not valid ({_why(exc)}). Reply again with the corrected JSON object only.")]
    raise StepFailed(schema.__name__)
