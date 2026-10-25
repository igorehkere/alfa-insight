"""Our bounded workflow on LangGraph; no prebuilt agent and no automatic retries.

The compiled graph is shared, request data is isolated. Checkpoint persistence is
deliberately not enabled: Igor persists the final analysis and chat context.
"""
from dataclasses import dataclass
from functools import lru_cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.agent.language import compact_warnings, ensure_human
from app.agent.errors import EvidenceError, InvalidInput
from app.agent.evidence import bank_evidence_gap, decision_rates
from app.agent.schema import AgentAnalysis, InsightResult
from app.agent.service import (compact_profile, complete_json, event_date, execute_plan,
                               make_context, news_evidence, tool_call)
from app.agent.state import AgentLimits, AgentState, Plan
from app.agent.tools import validate_profile
from app.agent.verifier import verify_result
from app.agent.relevance import RelevanceEngine, news_brief, no_relevance, normalize_assessment


@dataclass
class GraphContext:
    tools: object
    limits: AgentLimits


class Workflow(TypedDict, total=False):
    agent: AgentState
    plan: Plan
    replans: int
    repairs: int
    enough: bool
    context: dict
    result: InsightResult | None
    error: str | None
    analysis: dict
    schema_errors: list[dict]


async def load_company_and_news(data: Workflow, runtime: Runtime[GraphContext]):
    state, tools, limits = data["agent"], runtime.context.tools, runtime.context.limits
    raw = await tool_call(state, limits, "get_company_profile", lambda: tools.get_company_profile(state.company_id))
    state.profile = compact_profile(validate_profile(raw, state.company_id))
    state.news = dict(await tool_call(state, limits, "get_news", lambda: tools.get_news(state.news_id)))
    if state.news.get("id") != state.news_id or not state.news.get("body", "").strip():
        raise InvalidInput("Для анализа нужен непустой текст выбранной новости")
    if len(state.news["body"]) > 8000:
        state.warnings.append("Анализ ограничен первыми 8000 символами новости; полный текст не анализировался.")
    state.news["body"] = state.news["body"][:8000]
    event_date(state.news)  # Validate before spending tokens.
    state.evidence[state.news_id] = news_evidence(state.news)
    if state.news.get("provenance") in {"synthetic_fixture", "manual_submission"}:
        state.warnings.append("Новость тестовая или добавлена вручную; публикация не подтверждена.")
    if state.news.get("body_is_summary"):
        state.warnings.append("Доступно только краткое описание новости, не полный текст публикации.")
    if not state.news.get("published_at"):
        state.warnings.append("Дата новости неизвестна; поиск ограничен датой анализа.")
    state.warnings.append("Риски и возможности — условные выводы AI; автоматическая проверка не заменяет экспертную оценку.")
    return {"agent": state}


async def plan_analysis(data: Workflow, runtime: Runtime[GraphContext]):
    state = data["agent"]
    stage = "replan" if data.get("replans", 0) else "plan"
    payload = {"profile": state.profile, "news": news_brief(state.news), "warnings": state.warnings,
               "business_context": state.business_context,
               "event_relevance": state.event_relevance,
               "previous_queries": state.queries,
               "instruction": "Составь короткие тематические запросы. При повторном поиске измени формулировку; отсутствие позиции банка допустимо."}
    try:
        plan = await complete_json(runtime.context.tools, state, runtime.context.limits, stage, payload, Plan)
    except EvidenceError as exc:
        if not getattr(exc, 'schema_errors', None):
            raise
        # A malformed auxiliary event description must not block the main analysis.
        # Use a bounded search query from the actual news; do not invent relevance
        # and do not spend another paid call repairing the auxiliary plan.
        query = (state.news.get('title') or state.news['body'])[:150].strip()
        plan = Plan(search_queries=[query])
        state.warnings.append('Модель не оформила план поиска; материалы подбираются по заголовку выбранной новости.')
        state.record('verify', 'Использован поиск по теме новости после ошибки формата плана', status='insufficient_evidence')
    if plan.relevance is not None and state.event_relevance is None:
        assessed = normalize_assessment(plan.relevance, state.business_context['facts'], news_brief(state.news))
        if assessed is not None or plan.relevance.level == 'none':
            state.event_relevance = assessed or no_relevance()
            engine = getattr(runtime.context.tools, 'relevance_engine', None)
            if engine is not None:
                engine.remember_assessment(state.business_context, state.news, assessed)
    if (state.event_relevance or {}).get('level') == 'none':
        plan.need_counterparties = False
        plan.related_news_query = None
    state.record("plan", f"Подготовлен план поиска; банковских запросов: {len(plan.search_queries)}")
    return {"plan": plan}


async def describe_company(data: Workflow, runtime: Runtime[GraphContext]):
    state, tools, limits = data['agent'], runtime.context.tools, runtime.context.limits
    engine = getattr(tools, 'relevance_engine', None) or RelevanceEngine(tools.llm)
    async def generate(payload, model):
        return await complete_json(tools, state, limits, 'company_profile', payload, model)
    state.business_context = await engine.company_context(
        state.profile, generate=generate, allow_llm=limits.max_llm_calls >= 3)
    cached = engine.cached_assessments(state.business_context, [state.news])
    if state.news_id in cached:
        state.event_relevance = cached[state.news_id] or no_relevance()
    state.record('plan', 'Отделены сведения компании от отраслевых предположений; подготовлен контекст влияния событий')
    if engine.enabled and not state.business_context['inferred']:
        state.warnings.append('Развёрнутый профиль компании недоступен; используются только исходные сведения.')
    return {'agent': state}


async def find_evidence(data: Workflow, runtime: Runtime[GraphContext]):
    state = data["agent"]
    if (state.event_relevance or {}).get('level') == 'none':
        state.record('execute', 'Содержательная связь с компанией не установлена; дополнительный поиск не требуется')
        return {'agent': state}
    await execute_plan(data["plan"], state, runtime.context.tools, runtime.context.limits, event_date(state.news))
    return {"agent": state}


async def assess_evidence(data: Workflow):
    state = data["agent"]
    banks = {sid: item for sid, item in state.evidence.items() if item["source"]["type"] == "bank"}
    gaps = []
    for sid, item in banks.items():
        gap = bank_evidence_gap(state.news, item, event_date(state.news))
        if gap:
            gaps.append(gap)
            state.evidence.pop(sid)
    # Conflicting decisions on the same date cannot both support a bank position.
    by_date = {}
    for sid, item in banks.items():
        if sid in state.evidence:
            by_date.setdefault(item["published_at"], []).append((sid, decision_rates(item["text"])))
    for rows in by_date.values():
        rates = set().union(*(values for _, values in rows))
        if len(rates) > 1:
            gaps.append("Противоречивые значения решения по ставке в материалах одной даты")
            for sid, values in rows:
                if values:
                    state.evidence.pop(sid, None)
    enough = any(item["source"]["type"] == "bank" for item in state.evidence.values())
    if state.news.get("body_is_summary"):
        state.record("verify", "Покрытие ограничено описанием новости; полный текст недоступен", status="insufficient_evidence")
    details = "; ".join(dict.fromkeys(gaps)) if gaps else (
        "Найдены сверенные банковские выдержки по теме и дате; это эвристическая оценка" if enough
        else "Банковских доказательств по теме и дате недостаточно")
    state.record("verify", details, status="ok" if enough and not gaps else "insufficient_evidence")
    return {"enough": enough}


async def after_assessment(data: Workflow, runtime: Runtime[GraphContext]):
    state, limits = data["agent"], runtime.context.limits
    if (not data["enough"] and (state.event_relevance or {}).get('level') != 'none' and data.get("replans", 0) == 0
            and state.llm_calls < limits.max_llm_calls - 2 and state.tool_calls < limits.max_tool_calls
            and state.output_limit - state.output_reserved > limits.analysis_tokens + limits.plan_tokens):
        return "refine_search"
    return "generate_analysis"


async def refine_search(data: Workflow):
    return {"replans": data.get("replans", 0) + 1}


async def generate_analysis(data: Workflow, runtime: Runtime[GraphContext]):
    state, tools = data["agent"], runtime.context.tools
    context = make_context(state, tools, event_date(state.news))
    from app.agent.chat import evidence_excerpt
    # Keep original evidence for verification and saved chat context; send short
    # exact excerpts alongside the summary rather than duplicating full articles.
    evidence = {}
    anchor = (state.event_relevance or {}).get('quote', '')
    for sid, item in context['evidence'].items():
        if item['source']['type'] == 'news':
            text = evidence_excerpt(item['text'], anchors=(anchor,), budget=1800) or item['text'][:1200]
            evidence[sid] = {**item, 'text': text}
        else:
            evidence[sid] = item
    try:
        result = await complete_json(tools, state, runtime.context.limits,
            "repair" if data.get("repairs", 0) else "analyze",
            {**context, "news": news_brief(state.news), "evidence": evidence,
             "warnings": state.warnings, "verification_error": data.get("error"),
             "schema_errors": data.get('schema_errors', [])}, InsightResult)
        state.record("execute", "Получен структурированный анализ; проверяем доказательства")
        return {"context": context, "result": result, "error": None, 'schema_errors': []}
    except EvidenceError as exc:
        return {"context": context, "result": None, "error": str(exc),
                'schema_errors': getattr(exc, 'schema_errors', [])}


async def verify_analysis(data: Workflow):
    error = data.get("error")
    if not error:
        try:
            verify_result(data["result"], data["context"])
        except EvidenceError as exc:
            error = str(exc)
    if error:
        data["agent"].record("verify", "Объяснение требует исправления: не подтверждены формулировка или доказательства", status="insufficient_evidence")
    return {"error": error}


async def after_verification(data: Workflow, runtime: Runtime[GraphContext]):
    if not data.get("error"):
        return "finish"
    if (data.get("repairs", 0) == 0 and data["agent"].llm_calls < runtime.context.limits.max_llm_calls
            and data["agent"].output_limit - data["agent"].output_reserved >= 500):
        return "repair_analysis"
    raise EvidenceError(data["error"])


async def repair_analysis(data: Workflow):
    return {"repairs": data.get("repairs", 0) + 1}


async def finish(data: Workflow):
    state, result = data["agent"], data["result"]
    result.warnings = compact_warnings([*result.warnings, *state.warnings])
    if result.bank_position.status == "not_found":
        result.warnings.append("Подтверждённая позиция банка по этому событию не найдена.")
        result.warnings.append("Качественная оценка AI по новости и профилю компании: точный размер влияния не определён; это не позиция Альфа-Банка.")
    available = [item for item in state.evidence.values() if item['source']['type'] == 'bank']
    used_ids = {source.id for source in result.sources}
    for item in available:
        if (item['source']['id'] in result.bank_position.source_ids
                and item.get('published_at', '') < data['context'].get('as_of', '')):
            result.ai_interpretation += f"\n\nМатериал Альфа-Банка от {item['published_at']} — исторический аналитический фон; он не устанавливает текущие условия договоров."
        if item['source']['id'] not in used_ids:
            state.record('verify', 'Найден проверенный банковский материал, но он не использован в выводе; дополнительных утверждений на его основе нет', status='insufficient_evidence')
    if result.suggested_actions:
        if not any(action in result.ai_interpretation for action in result.suggested_actions):
            result.ai_interpretation += '\n\nДоступные действия: ' + ' '.join(result.suggested_actions)
    result.warnings = compact_warnings(result.warnings)
    ensure_human(*(row['details'] for row in state.trace))
    state.record("verify", "Проверены источники, выдержки, признаки компании, даты и практические действия")
    return {"analysis": AgentAnalysis(result=result, agent_trace=state.trace).model_dump(mode="json")}


@lru_cache(maxsize=1)
def analysis_graph():
    graph = StateGraph(Workflow, context_schema=GraphContext)
    for node in (load_company_and_news, describe_company, plan_analysis, find_evidence, assess_evidence,
                 refine_search, generate_analysis, verify_analysis, repair_analysis, finish):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "load_company_and_news")
    graph.add_edge("load_company_and_news", "describe_company")
    graph.add_edge("describe_company", "plan_analysis")
    graph.add_edge("plan_analysis", "find_evidence")
    graph.add_edge("find_evidence", "assess_evidence")
    graph.add_conditional_edges("assess_evidence", after_assessment, ["refine_search", "generate_analysis"])
    graph.add_edge("refine_search", "plan_analysis")
    graph.add_edge("generate_analysis", "verify_analysis")
    graph.add_conditional_edges("verify_analysis", after_verification, ["repair_analysis", "finish"])
    graph.add_edge("repair_analysis", "generate_analysis")
    graph.add_edge("finish", END)
    return graph.compile()
