"""Bounded PLAN -> tools -> ANALYZE -> VERIFY -> optional correction."""
from __future__ import annotations

import asyncio
import copy
import json
from dataclasses import dataclass
from datetime import date, datetime

from pydantic import ValidationError

from app.agent.errors import AgentUnavailable, EvidenceError, InvalidInput
from app.agent.llm_client import LLMError
from app.agent.prompts import PROMPT_VERSION, messages
from app.agent.schema import AgentAnalysis, AnalyzeRequest, AnswerResponse, AskRequest, InsightResult
from app.agent.state import AgentLimits, AgentState, Plan
from app.agent.tools import validate_profile
from app.agent.verifier import verify_answer, verify_result


@dataclass
class AnalysisRun:
    analysis: dict
    context: dict
    usage: list[dict]


def compact_profile(profile: dict) -> dict:
    # No raw_profile, person identifiers, or complete payment graph sent to a model.
    keys = ("company_id", "industry", "industry_code", "region", "group_id", "features",
            "payment_summary", "data_notes", "provenance")
    return {key: copy.deepcopy(profile[key]) for key in keys if key in profile}


def event_date(news: dict) -> date:
    value = news.get("published_at")
    if not value:
        return date.today()
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).date()
    except (TypeError, ValueError, AttributeError) as exc:
        raise InvalidInput("Некорректная дата новости") from exc


def news_evidence(news: dict) -> dict:
    return {"source": {"id": news["id"], "type": "news", "title": news["title"],
                       "url": news.get("source_url")},
            "text": news["body"], "published_at": news.get("published_at"),
            "provenance": news.get("provenance", "unknown")}


async def complete_json(tools, state, limits, stage, payload, model):
    if len(json.dumps(payload, ensure_ascii=False)) > 80000:
        raise InvalidInput("Контекст слишком велик; адаптер должен вернуть ограниченные выборки")
    if state.llm_calls >= limits.max_llm_calls:
        raise EvidenceError("Исчерпан лимит обращений к модели")
    state.llm_calls += 1
    budget = limits.plan_tokens if stage in {"plan", "replan"} else limits.analysis_tokens
    try:
        reply = await tools.llm.complete(messages(stage, payload, model.model_json_schema()), max_tokens=budget)
    except LLMError as exc:
        raise AgentUnavailable(str(exc)) from None
    except EvidenceError as exc:
        metrics = getattr(exc, "response_metrics", None)
        if metrics:
            state.usage.append({"model": metrics["model"], "stage": stage,
                                "tokens": {key: metrics.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
                                "elapsed_seconds": metrics["elapsed_seconds"], "finish_reason": metrics["finish_reason"]})
        raise
    state.usage.append({"model": reply.model, "stage": stage,
                        "tokens": reply.usage.model_dump() if reply.usage else None,
                        "elapsed_seconds": reply.elapsed_seconds,
                        "finish_reason": reply.finish_reason})
    if reply.finish_reason == "length":
        raise EvidenceError("Ответ модели обрезан лимитом токенов")
    text = reply.text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    if len(text) > 40000:
        raise EvidenceError("Слишком большой ответ модели")
    try:
        return model.model_validate_json(text)
    except ValidationError:
        # Do not expose raw output or its potentially sensitive validation values.
        raise EvidenceError("Ответ модели не соответствует согласованной JSON-схеме") from None


async def tool_call(state, limits, name, operation):
    if state.tool_calls >= limits.max_tool_calls:
        raise EvidenceError("Исчерпан лимит инструментальных шагов")
    state.tool_calls += 1
    result = await operation()
    empty = not result or (isinstance(result, dict) and ("items" in result and not result["items"]))
    count = len(result) if isinstance(result, list) else len(result.get("items", [])) if "items" in result else 1
    state.record("execute", f"Получено записей: {count}", name, "empty" if empty else "ok")
    return result


def make_context(state, tools, as_of):
    return {"company_id": state.company_id, "news_id": state.news_id,
            "profile": state.profile, "news": state.news, "evidence": state.evidence,
            "as_of": as_of.isoformat(), "search_queries": list(state.queries), "prompt_version": PROMPT_VERSION,
            "corpus_version": tools.corpus_version}


async def execute_plan(plan, state, tools, limits, as_of):
    for query in plan.search_queries:
        query = query.strip()
        if not query or len(query) > 500:
            raise EvidenceError("План содержит недопустимый поисковый запрос")
        if query in state.queries:
            continue
        if state.tool_calls >= limits.max_tool_calls:
            state.record("verify", "Лимит поиска исчерпан; используем только полученные доказательства",
                         status="insufficient_evidence")
            break
        state.queries.append(query)
        rows = await tool_call(state, limits, "search_bank_analytics",
                               lambda: tools.search_bank_analytics(query, 5, as_of=as_of))
        state.trace[-1]["details"] = f"Запрос: {query}; найдено фрагментов: {len(rows)}"
        accepted = 0
        for row in rows[:5]:
            if row.get("reviewed") is not True or row.get("origin") != "bank":
                continue
            try:
                published = date.fromisoformat(row["published_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if published > as_of or (as_of - published).days > 365:
                continue
            source_id = "bank:" + row["chunk_id"]
            bank_count = sum(e["source"]["type"] == "bank" for e in state.evidence.values())
            if source_id not in state.evidence and bank_count >= 5:
                continue
            source = {"id": source_id, "type": "bank", "title": row["document_title"],
                      "url": row.get("source_url"), "document_id": row["document_id"],
                      "document_title": row["document_title"], "page_number": row["page_number"]}
            state.evidence[source_id] = {"source": source, "text": row["text"][:4000],
                                         "published_at": row["published_at"], "reviewed": True,
                                         "origin": "bank"}
            accepted += 1
            state.record("verify", f"Проверен документ {row['document_id']}, страница {row['page_number']}, фрагмент {row['chunk_id']}")
        if not accepted:
            state.record("verify", "Подходящих сверенных фрагментов с допустимой датой нет",
                         status="insufficient_evidence")
    extra = {}
    if plan.need_counterparties and state.tool_calls < limits.max_tool_calls:
        extra["payment_links"] = await tool_call(state, limits, "get_company_counterparties",
            lambda: tools.get_company_counterparties(state.company_id, "outgoing"))
        state.profile["payment_links"] = extra["payment_links"]
    if plan.related_news_query and state.tool_calls < limits.max_tool_calls:
        rows = await tool_call(state, limits, "search_related_news",
            lambda: tools.search_related_news(plan.related_news_query, 3))
        for news in rows[:3]:
            if news["id"] in state.evidence or not news.get("body", "").strip():
                continue
            if event_date(news) > as_of:
                continue
            news = {**news, "body": news["body"][:8000]}
            state.evidence[news["id"]] = news_evidence(news)
    return extra


async def _run(company_id, news_id, tools, limits, state):
    # Local import avoids a service/graph import cycle. No model is loaded here.
    from app.agent.graph import analysis_graph, GraphContext
    try:
        output = await analysis_graph().ainvoke(
            {"agent": state, "replans": 0, "repairs": 0},
            context=GraphContext(tools=tools, limits=limits),
            config={"recursion_limit": 24},
        )
        return AnalysisRun(output["analysis"], copy.deepcopy(output["context"]), state.usage)
    except Exception as exc:
        if hasattr(exc, "__dict__"):
            exc.usage = state.usage
        raise


async def run_analysis(company_id: str, news_id: str, tools, *, limits: AgentLimits | None = None) -> AnalysisRun:
    try:
        request = AnalyzeRequest(company_id=company_id, news_id=news_id)
        company_id, news_id = request.company_id, request.news_id
    except ValidationError:
        raise InvalidInput("Нужны непустые company_id и news_id") from None
    if len(company_id) > 100 or len(news_id) > 100:
        raise InvalidInput("ID слишком длинный")
    limits = limits or AgentLimits()
    state = AgentState(company_id, news_id)
    try:
        return await asyncio.wait_for(_run(company_id, news_id, tools, limits, state), limits.deadline_seconds)
    except TimeoutError:
        error = AgentUnavailable("Истекло общее время подготовки анализа")
        error.usage = state.usage
        raise error from None


async def analyze(company_id: str, news_id: str, tools) -> dict:
    """Contract entry point. Use run_analysis if the API also saves chat context."""
    return (await run_analysis(company_id, news_id, tools)).analysis


async def answer_question(insight: dict, question: str, tools, *, history=None, turn_count=None, limits=None) -> dict:
    """Short grounded chat; history is bounded to a three-question cycle.

    Persist answers in Igor's repository. Pass either the complete history or
    its latest two turns plus the total turn_count. Invalid company/context/input
    still raises InvalidInput; generation failures yield an explicit general answer.
    """
    from app.agent.chat import answer_question as respond
    return await respond(insight, question, tools, history=history, turn_count=turn_count, limits=limits)
