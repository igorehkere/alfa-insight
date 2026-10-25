"""Internal contracts. Public API DTOs in schema.py remain unchanged."""
from dataclasses import dataclass, field
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field
from app.agent.relevance import EventRelevance


class Plan(BaseModel):
    model_config = ConfigDict(extra="forbid")
    search_queries: list[str] = Field(min_length=1, max_length=2)
    need_counterparties: bool = False
    related_news_query: str | None = Field(default=None, max_length=500)
    relevance: EventRelevance | None = None


class AgentLimits(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    max_tool_calls: int = Field(default=7, ge=3, le=12)
    max_llm_calls: int = Field(default=4, ge=2, le=4)
    deadline_seconds: float = Field(default=120, gt=0, le=180)
    plan_tokens: int = Field(default=1000, ge=100, le=4000)
    analysis_tokens: int = Field(default=3000, ge=500, le=6000)


@dataclass
class AgentState:
    company_id: str
    news_id: str
    profile: dict = field(default_factory=dict)
    news: dict = field(default_factory=dict)
    business_context: dict = field(default_factory=dict)
    event_relevance: dict | None = None
    evidence: dict = field(default_factory=dict)
    trace: list = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    usage: list[dict] = field(default_factory=list)
    queries: list[str] = field(default_factory=list)
    tool_calls: int = 0
    llm_calls: int = 0

    output_limit: int = 10000
    output_reserved: int = 0

    def record(self, phase: Literal["plan", "execute", "verify"], details: str,
               tool: str | None = None, status: str = "ok"):
        self.trace.append({"step": len(self.trace) + 1, "phase": phase,
                           "tool": tool, "status": status, "details": details})
