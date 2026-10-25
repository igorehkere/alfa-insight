"""Bounded PLAN -> tools -> ANALYZE -> VERIFY -> optional correction."""
from __future__ import annotations

import asyncio
import copy
import json
import logging
from time import perf_counter
from dataclasses import dataclass
from datetime import date, datetime

from pydantic import ValidationError

from app.agent.errors import AgentUnavailable, EvidenceError, InvalidInput
from app.agent.evidence import bank_evidence_gap
from app.agent.llm_client import LLMError
from app.agent.language import profile_facts
from app.agent.prompts import PROMPT_VERSION, messages
from app.agent.schema import AgentAnalysis, AnalyzeRequest, AnswerResponse, AskRequest, InsightResult
from app.agent.state import AgentLimits, AgentState, Plan
from app.agent.tools import validate_profile
from app.agent.relevance import PROFILE_TOKENS
from app.agent.verifier import verify_answer, verify_result


logger = logging.getLogger(__name__)


def schema_issues(error, model):
    """Expose schema paths/types only, never model output or validation values."""
    allowed = set()
    def visit(node):
        if isinstance(node, dict):
            allowed.update(node.get('properties', {}))
            for value in node.values():
                visit(value)
        elif isinstance(node, list):
            for value in node:
                visit(value)
    visit(model.model_json_schema())
    return [{'path': '.'.join(str(part) if isinstance(part, int) or part in allowed else '*'
                             for part in row['loc']) or '$',
             'type': row['type']}
            for row in error.errors(include_input=False, include_context=False, include_url=False)[:6]]


def schema_error_message(stage, issues):
    names = {'company_profile': 'описание компании', 'plan': 'план поиска',
             'replan': 'уточнённый план поиска', 'analyze': 'анализ новости', 'repair': 'анализ новости'}
    labels = {'relevance': 'описание события', 'what_happened': 'факты новости',
              'why_relevant': 'связь с компанией', 'risks': 'риски', 'opportunities': 'возможности',
              'bank_position': 'позиция банка', 'sources': 'источники',
              'suggested_actions': 'предлагаемые действия', 'ai_interpretation': 'объяснение',
              'search_queries': 'поисковые запросы', 'warnings': 'предупреждения'}
    fields = list(dict.fromkeys(labels.get(row['path'].split('.')[0], 'структура ответа') for row in issues))
    return 'Модель неверно оформила ' + names.get(stage, 'ответ') + ': ' + ', '.join(fields) + '. Требуется исправление формата.'


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
    budget = (PROFILE_TOKENS if stage == "company_profile" else
              limits.plan_tokens if stage in {"plan", "replan"} else limits.analysis_tokens)
    remaining = state.output_limit - state.output_reserved
    # Reserve pessimistically when the unchanged client may try a second endpoint.
    attempts = 2 if getattr(tools.llm, "_fallback", None) is not None and not getattr(tools.llm, "_use_fallback", False) else 1
    budget = min(budget, remaining // attempts)
    if budget < 100:
        raise EvidenceError("Исчерпан общий бюджет ответа")
    state.llm_calls += 1
    state.output_reserved += budget * attempts
    diagnostic_start = getattr(tools.llm, "http_attempts_total", len(getattr(tools.llm, "diagnostics", ())))
    if stage != "company_profile":
        payload = {**payload, "human_profile": profile_facts(payload.get("profile") or payload.get("context", {}).get("profile", {}))}
    prepared = messages(stage, payload, model.model_json_schema())
    prompt_estimate = (len(json.dumps(prepared, ensure_ascii=False)) + 2) // 3
    def diagnostic_rows():
        count = getattr(tools.llm, "http_attempts_total", len(getattr(tools.llm, "diagnostics", ()))) - diagnostic_start
        return list(getattr(tools.llm, "diagnostics", ()))[-count:] if count > 0 else []

    def reconcile_usage():
        diagnostics = diagnostic_rows()
        if diagnostics and all(row.get("completion_tokens") is not None for row in diagnostics):
            state.output_reserved -= budget * attempts
            state.output_reserved += sum(row["completion_tokens"] for row in diagnostics)

    started = perf_counter()
    try:
        reply = await tools.llm.complete(prepared, max_tokens=budget)
    except LLMError as exc:
        reconcile_usage()
        state.usage.append({'stage': stage, 'tokens': None, 'output_reserved': budget * attempts,
                            'http_attempts': len(diagnostic_rows()), 'http_usage': diagnostic_rows(),
                            'input_tokens_estimate': prompt_estimate, 'estimate_method': 'characters/3, approximate'})
        logger.info("agent stage=%s reason=provider_unavailable attempt=%s elapsed=%.3f", stage, state.llm_calls, perf_counter() - started)
        raise AgentUnavailable("Не удалось подготовить объяснение; сервис временно недоступен") from None
    except EvidenceError as exc:
        reconcile_usage()
        metrics = getattr(exc, "response_metrics", None)
        if metrics:
            state.usage.append({"model": metrics["model"], "stage": stage,
                                "tokens": {key: metrics.get(key) for key in ("prompt_tokens", "completion_tokens", "total_tokens")},
                                "elapsed_seconds": metrics["elapsed_seconds"], "finish_reason": metrics["finish_reason"],
                                "output_reserved": budget * attempts, "http_attempts": len(diagnostic_rows()),
                                "http_usage": diagnostic_rows(), "input_tokens_estimate": prompt_estimate,
                                "estimate_method": "characters/3, approximate"})
        raise
    reconcile_usage()
    state.usage.append({"model": reply.model, "stage": stage,
                        "tokens": reply.usage.model_dump() if reply.usage else None,
                        "elapsed_seconds": reply.elapsed_seconds,
                        "finish_reason": reply.finish_reason,
                        "output_reserved": budget * attempts,
                        "input_tokens_estimate": prompt_estimate, "estimate_method": "characters/3, approximate",
                        "http_attempts": len(diagnostic_rows()),
                        "http_usage": [dict(row) for row in diagnostic_rows()]})
    logger.info("agent stage=%s reason=completed attempt=%s elapsed=%.3f usage=%s",
                stage, state.llm_calls, perf_counter() - started, state.usage[-1]["tokens"])
    if reply.finish_reason == "length":
        raise EvidenceError("Ответ модели обрезан лимитом токенов")
    text = reply.text.strip()
    if text.startswith("```json") and text.endswith("```"):
        text = text[7:-3].strip()
    if len(text) > 40000:
        raise EvidenceError("Слишком большой ответ модели")
    try:
        return model.model_validate_json(text)
    except ValidationError as exc:
        issues = schema_issues(exc, model)
        logger.warning('agent stage=%s schema_errors=%s', stage, issues)
        error = EvidenceError(schema_error_message(stage, issues))
        error.schema_errors = issues
        raise error from None


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
    current_bank = any(item['source']['type'] == 'bank' and item.get('eligible_for_current_position', True)
                       for item in state.evidence.values())
    return {"company_id": state.company_id, "news_id": state.news_id,
            "profile": state.profile, "news": state.news, "evidence": state.evidence,
            "business_context": state.business_context, "event_relevance": state.event_relevance,
            "analysis_policy": {"mode": "bank_supported" if current_bank else "qualitative",
                                "require_impacts": not getattr(tools.llm, 'offline', False)},
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
        state.trace[-1]["details"] = f"По теме события найдено фрагментов: {len(rows)}"
        accepted = 0
        for row in rows[:5]:
            if row.get("reviewed") is not True or row.get("origin") != "bank":
                continue
            try:
                published = date.fromisoformat(row["published_at"])
            except (KeyError, TypeError, ValueError):
                continue
            if published > as_of:
                continue
            gap = bank_evidence_gap(state.news, row, as_of)
            if gap:
                state.record("verify", gap, status="insufficient_evidence")
                continue
            from app.agent.chat import evidence_excerpt
            excerpt = evidence_excerpt(row['text'], budget=4000)
            if not excerpt:
                continue
            source_id = "bank:" + row["chunk_id"]
            bank_count = sum(e["source"]["type"] == "bank" for e in state.evidence.values())
            if source_id not in state.evidence and bank_count >= 5:
                continue
            source = {"id": source_id, "type": "bank", "title": row["document_title"],
                      "url": row.get("source_url"), "document_id": row["document_id"],
                      "document_title": row["document_title"], "page_number": row["page_number"]}
            state.evidence[source_id] = {"source": source, "text": excerpt,
                                         "published_at": row["published_at"], "reviewed": True,
                                         "origin": "bank",
                                         "eligible_for_current_position": (as_of - published).days <= 365,
                                         "evidence_role": "historical_background" if published < as_of else "event_comment"}
            accepted += 1
            state.record("verify", f"Проверены происхождение, дата и страница банковского материала")
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
    """Grounded chat with bounded task memory restored from user messages.

    Persist answers in Igor's repository. Pass either the complete history or
    a bounded set of recent turns plus the total turn_count. Invalid company/context/input
    still raises InvalidInput; generation failures yield an explicit general answer.
    """
    from app.agent.chat import answer_question as respond
    return await respond(insight, question, tools, history=history, turn_count=turn_count, limits=limits)
