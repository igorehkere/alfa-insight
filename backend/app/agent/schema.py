from __future__ import annotations

from typing import Annotated, Literal, Self

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, model_validator


NonEmptyText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class AgentSchema(BaseModel):
    model_config = ConfigDict(extra="forbid")


class AnalyzeRequest(AgentSchema):
    company_id: NonEmptyText = Field(max_length=100)
    news_id: NonEmptyText = Field(max_length=100)
    force_refresh: bool = False


class AskRequest(AgentSchema):
    question: NonEmptyText = Field(max_length=2000)


class NewsSource(AgentSchema):
    id: NonEmptyText
    type: Literal["news"] = "news"
    title: NonEmptyText
    url: NonEmptyText | None = None


class BankSource(AgentSchema):
    id: NonEmptyText
    type: Literal["bank"] = "bank"
    title: NonEmptyText
    url: NonEmptyText | None = None
    document_id: NonEmptyText
    document_title: NonEmptyText
    page_number: int = Field(ge=1)


Source = Annotated[NewsSource | BankSource, Field(discriminator="type")]


class SourcedStatement(AgentSchema):
    text: NonEmptyText
    source_ids: list[NonEmptyText] = Field(min_length=1)


class RelevanceStatement(AgentSchema):
    text: NonEmptyText
    company_fields: list[NonEmptyText] = Field(min_length=1)


class ImpactStatement(SourcedStatement):
    certainty: NonEmptyText


class BankPosition(AgentSchema):
    status: Literal["found", "not_found"]
    text: NonEmptyText | None = None
    source_ids: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_evidence(self) -> Self:
        if self.status == "found" and (self.text is None or not self.source_ids):
            raise ValueError("Found bank position requires text and source_ids")
        if self.status == "not_found" and (self.text is not None or self.source_ids):
            raise ValueError("Missing bank position requires null text and no sources")
        return self


class InsightResult(AgentSchema):
    what_happened: list[SourcedStatement]
    why_relevant: list[RelevanceStatement]
    risks: list[ImpactStatement]
    opportunities: list[ImpactStatement]
    bank_position: BankPosition
    ai_interpretation: NonEmptyText
    suggested_actions: list[NonEmptyText]
    sources: list[Source]
    warnings: list[NonEmptyText] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_source_references(self) -> Self:
        sources_by_id = {source.id: source for source in self.sources}
        if len(sources_by_id) != len(self.sources):
            raise ValueError("Source IDs must be unique")
        referenced_ids = set(self.bank_position.source_ids)
        for statement in [*self.what_happened, *self.risks, *self.opportunities]:
            referenced_ids.update(statement.source_ids)
        missing_ids = referenced_ids - sources_by_id.keys()
        if missing_ids:
            raise ValueError(f"Unknown source IDs: {', '.join(sorted(missing_ids))}")
        for source_id in self.bank_position.source_ids:
            if sources_by_id[source_id].type != "bank":
                raise ValueError("Bank position must reference bank documents")
        return self


class AgentTraceStep(AgentSchema):
    """Public execution summary; exclude raw prompts and secrets."""

    step: int = Field(ge=1)
    phase: Literal["plan", "execute", "verify"]
    tool: NonEmptyText | None = None
    status: Literal["ok", "empty", "insufficient_evidence", "error"]
    details: NonEmptyText


class AgentAnalysis(AgentSchema):
    """Agent output before the API service assigns a persisted insight ID."""

    result: InsightResult
    agent_trace: list[AgentTraceStep]


class InsightResponse(AgentAnalysis):
    insight_id: NonEmptyText
    company_id: NonEmptyText
    news_id: NonEmptyText
    status: Literal["completed"] = "completed"


class AnswerResponse(AgentSchema):
    """Validate source IDs against saved insight context in the verifier."""

    answer: NonEmptyText
    source_ids: list[NonEmptyText] = Field(default_factory=list)
    warnings: list[NonEmptyText] = Field(default_factory=list)


class TraceResponse(AgentSchema):
    items: list[AgentTraceStep]


class TokenUsage(BaseModel):
    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = Field(ge=0)
    completion_tokens: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class LLMReply(BaseModel):
    model_config = ConfigDict(frozen=True)

    text: str = Field(min_length=1)
    model: str
    usage: TokenUsage | None = None
    elapsed_seconds: float = Field(ge=0)
    finish_reason: str
