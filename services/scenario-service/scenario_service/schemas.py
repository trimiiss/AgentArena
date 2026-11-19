from __future__ import annotations

from datetime import datetime
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator

ScenarioType = Literal[
    "customer_support",
    "multi_turn",
    "tool_use",
    "error_handling",
    "conflicting_information",
    "missing_information",
    "policy_compliance",
    "concurrent_tasks",
]
Difficulty = Literal["easy", "medium", "hard"]
TaskStatus = Literal["in_progress", "completed", "failed", "needs_input"]
Dimension = Literal[
    "task_completion", "correctness", "tool_use", "policy", "response_quality", "error_handling", "latency", "cost"
]
RuleOperator = Literal["eq", "ne", "lt", "lte", "gt", "gte", "in", "contains", "exists"]


class Turn(BaseModel):
    role: Literal["user"] = "user"
    content: str = Field(min_length=1, max_length=20000)


class ToolResponse(BaseModel):
    """Returned when every key in ``when`` matches the call's arguments."""

    when: dict[str, Any] = Field(default_factory=dict)
    output: Any = None


class ToolFailure(BaseModel):
    """Scripted failure, which is how 'unexpected tool failures' are simulated."""

    on_calls: list[int] = Field(default_factory=list, description="1-based call numbers that fail.")
    error: str = "Tool temporarily unavailable"


class ToolDefinition(BaseModel):
    name: str = Field(min_length=1, max_length=128)
    description: str = ""
    parameters: dict[str, Any] = Field(default_factory=dict)
    responses: list[ToolResponse] = Field(default_factory=list)
    default_output: Any = None
    failure: ToolFailure | None = None


class ScenarioDefinition(BaseModel):
    system_prompt: str = Field(default="", max_length=20000)
    turns: list[Turn] = Field(min_length=1, max_length=50)
    tools: list[ToolDefinition] = Field(default_factory=list, max_length=50)
    context: dict[str, Any] = Field(default_factory=dict)
    max_agent_calls: int = Field(default=10, ge=1, le=100)


class ExpectedToolCall(BaseModel):
    name: str
    arguments: dict[str, Any] = Field(default_factory=dict, description="Subset that must appear in the call.")
    required: bool = True
    max_calls: int | None = Field(default=None, ge=1)


class BusinessRule(BaseModel):
    """``path`` reads ``output.<field>`` from structured output or ``tool.<name>.<arg>`` from tool calls."""

    path: str = Field(min_length=1, max_length=256)
    op: RuleOperator
    value: Any = None
    description: str = ""


class ExpectedOutcome(BaseModel):
    task_status: TaskStatus | None = "completed"
    exact_response: str | None = None
    reference_answer: str | None = None
    required_phrases: list[str] = Field(default_factory=list)
    forbidden_phrases: list[str] = Field(default_factory=list)
    response_schema: dict[str, Any] | None = None
    tool_calls: list[ExpectedToolCall] = Field(default_factory=list)
    forbidden_tools: list[str] = Field(default_factory=list)
    tool_order: list[str] = Field(default_factory=list)
    business_rules: list[BusinessRule] = Field(default_factory=list)
    must_handle_failure: bool = False
    failure_ack_phrases: list[str] = Field(default_factory=list)


class EvaluationCriteria(BaseModel):
    pass_threshold: float = Field(default=0.7, ge=0, le=1)
    weights: dict[Dimension, float] = Field(
        default_factory=lambda: {
            "task_completion": 3,
            "correctness": 3,
            "tool_use": 2,
            "policy": 3,
            "error_handling": 2,
            "response_quality": 1,
            "latency": 0.5,
            "cost": 0.5,
        }
    )
    critical_dimensions: list[Dimension] = Field(default_factory=lambda: ["policy"])
    latency_budget_ms: int = Field(default=5000, ge=1)
    token_budget: int | None = Field(default=None, ge=1)
    cost_budget_usd: float | None = Field(default=None, ge=0)
    similarity_threshold: float = Field(default=0.35, ge=0, le=1)


class VersionContent(BaseModel):
    definition: ScenarioDefinition
    expected: ExpectedOutcome = Field(default_factory=ExpectedOutcome)
    criteria: EvaluationCriteria = Field(default_factory=EvaluationCriteria)


class ScenarioCreate(VersionContent):
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=4000)
    scenario_type: ScenarioType
    difficulty: Difficulty = "medium"
    tags: list[str] = Field(default_factory=list, max_length=20)


class ScenarioUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    scenario_type: ScenarioType | None = None
    difficulty: Difficulty | None = None
    tags: list[str] | None = Field(default=None, max_length=20)


class VersionCreate(VersionContent):
    change_note: str = Field(default="", max_length=2000)


class VersionOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    scenario_id: UUID
    version: int
    definition: dict[str, Any]
    expected: dict[str, Any]
    criteria: dict[str, Any]
    change_note: str
    created_at: datetime


class ScenarioSummary(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str
    description: str
    scenario_type: str
    difficulty: str
    tags: list[str]
    latest_version: int
    parent_scenario_id: UUID | None
    created_at: datetime
    updated_at: datetime


class ScenarioOut(ScenarioSummary):
    current: VersionOut


class ScenarioList(BaseModel):
    items: list[ScenarioSummary]
    total: int


class ResolvedScenario(BaseModel):
    scenario_id: UUID
    version: int
    name: str
    scenario_type: str
    difficulty: str


class ResolveResponse(BaseModel):
    items: list[ResolvedScenario]


class SuiteItemIn(BaseModel):
    scenario_id: UUID
    pinned_version: int | None = Field(default=None, ge=1)


class SuiteCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=4000)
    items: list[SuiteItemIn] = Field(default_factory=list, max_length=500)


class SuiteUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    items: list[SuiteItemIn] | None = Field(default=None, max_length=500)


class SuiteItemOut(BaseModel):
    scenario_id: UUID
    name: str
    scenario_type: str
    difficulty: str
    pinned_version: int | None
    latest_version: int


class SuiteOut(BaseModel):
    id: UUID
    org_id: UUID
    name: str
    description: str
    items: list[SuiteItemOut]
    created_at: datetime
    updated_at: datetime


class SuiteList(BaseModel):
    items: list[SuiteOut]
    total: int


class DatasetCreate(BaseModel):
    name: str = Field(min_length=2, max_length=200)
    description: str = Field(default="", max_length=4000)
    rows: list[dict[str, Any]] = Field(default_factory=list)


class DatasetUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=2, max_length=200)
    description: str | None = Field(default=None, max_length=4000)
    rows: list[dict[str, Any]] | None = None


class DatasetOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    org_id: UUID
    name: str
    description: str
    rows: list[dict[str, Any]]
    created_at: datetime
    updated_at: datetime


class DatasetList(BaseModel):
    items: list[DatasetOut]
    total: int


Strategy = Literal["typos", "lowercase", "uppercase", "polite", "urgent", "filler", "no_punctuation"]


class VariationRequest(BaseModel):
    """Either perturb the user turns, or fill the template from a dataset."""

    count: int = Field(default=5, ge=1, le=100)
    seed: int = 42
    strategies: list[Strategy] = Field(default_factory=lambda: ["typos", "lowercase", "polite", "urgent", "filler"])
    dataset_id: UUID | None = None

    @model_validator(mode="after")
    def needs_a_source(self) -> VariationRequest:
        if not self.strategies and self.dataset_id is None:
            raise ValueError("Provide at least one strategy or a dataset_id")
        return self


class VariationResponse(BaseModel):
    created: list[ScenarioSummary]


class StarterPackResponse(BaseModel):
    scenarios_created: int
    suite_id: UUID
    dataset_id: UUID
