"""The blueprint schema. Every model rejects unknown fields, so a generator cannot smuggle extra structure in."""
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

Id = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9-]{0,40}$")]
Name = Annotated[str, StringConstraints(min_length=1, max_length=120)]
Text = Annotated[str, StringConstraints(max_length=600)]
ToolRef = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+$", max_length=120)]
FrameworkRef = Annotated[str, StringConstraints(pattern=r"^(pypi|npm):[@A-Za-z0-9_./-]+$", max_length=120)]
OwaspId = Annotated[str, StringConstraints(pattern=r"^LLM\d{2}$")]

Capability = Literal["read", "write", "exec", "network", "payment"]
HIGH_RISK_CAPABILITIES = ("write", "exec", "network", "payment")
TOPOLOGIES = ("workflow", "router", "orchestrator-workers", "supervisor", "evaluator-optimizer", "autonomous")
Topology = Literal["workflow", "router", "orchestrator-workers", "supervisor", "evaluator-optimizer", "autonomous"]


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Profile(Strict):
    latency: Literal["interactive", "near_real_time", "batch"]
    volume_per_day: int = Field(ge=1, le=100_000_000)
    risk: Literal["low", "medium", "high"]
    data_sensitivity: Literal["public", "internal", "pii", "regulated"]
    autonomy: Literal["suggest", "act_with_approval", "act_autonomously"]
    budget_usd_month: int = Field(ge=0, le=10_000_000)
    capabilities: list[Capability] = Field(default_factory=list, max_length=5)
    open_ended: bool = False
    task_kinds: int = Field(default=1, ge=1, le=50)
    parallelizable: bool = False
    knowledge: bool = False
    quality_critical: bool = False


class Mitigation(Strict):
    capability: Capability
    by: Id


class Component(Strict):
    id: Id
    role: Annotated[str, StringConstraints(max_length=160)]
    kind: Literal["model", "tool", "memory", "retrieval", "human_gate"]
    name: Name
    model: str | None = Field(default=None, max_length=80)
    tool_ref: ToolRef | None = None
    framework_ref: FrameworkRef | None = None
    capabilities: list[Capability] = Field(default_factory=list, max_length=5)
    mitigations: list[Mitigation] = Field(default_factory=list, max_length=10)


class Guardrail(Strict):
    id: Id
    kind: Literal["input_filter", "output_filter", "approval_gate", "rate_limit", "sandbox", "audit_log"]
    protects: list[Id] = Field(default_factory=list, max_length=30)
    description: Text


class FailureMode(Strict):
    id: Id
    description: Text
    component: Id
    mitigation: Id


class Threat(Strict):
    owasp: OwaspId
    component: Id
    mitigation: Id
    note: Text = ""


class EvalItem(Strict):
    metric: Annotated[str, StringConstraints(min_length=1, max_length=160)]
    method: Text
    threshold: Annotated[str, StringConstraints(max_length=80)]


class Step(Strict):
    id: Id
    component: Id
    input_tokens: int = Field(ge=0, le=1_000_000)
    output_tokens: int = Field(ge=0, le=1_000_000)
    calls: float = Field(default=1.0, gt=0, le=100)


class Deployment(Strict):
    runtime: Annotated[str, StringConstraints(min_length=1, max_length=200)]
    notes: Text = ""


class Blueprint(Strict):
    id: Id
    title: Name
    summary: Text
    use_case: Annotated[str, StringConstraints(max_length=80)]
    version: int = Field(ge=1, le=10_000)
    changelog: list[Annotated[str, StringConstraints(max_length=300)]] = Field(min_length=1, max_length=100)
    profile: Profile
    topology: Topology
    patterns: list[Id] = Field(default_factory=list, max_length=12)
    components: list[Component] = Field(min_length=1, max_length=30)
    guardrails: list[Guardrail] = Field(default_factory=list, max_length=30)
    flow: list[Step] = Field(min_length=1, max_length=30)
    failure_modes: list[FailureMode] = Field(min_length=1, max_length=40)
    threats: list[Threat] = Field(default_factory=list, max_length=40)
    eval_plan: list[EvalItem] = Field(min_length=1, max_length=20)
    observability: list[Annotated[str, StringConstraints(max_length=200)]] = Field(min_length=1, max_length=20)
    deployment: Deployment
