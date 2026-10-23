"""Our bounded workflow on LangGraph; no prebuilt agent and no automatic retries.

The compiled graph is shared, request data is isolated. Checkpoint persistence is
deliberately not enabled: Igor persists the final analysis and chat context.
"""
from dataclasses import dataclass
from functools import lru_cache
from typing import TypedDict

from langgraph.graph import END, START, StateGraph
from langgraph.runtime import Runtime

from app.agent.errors import EvidenceError, InvalidInput
from app.agent.schema import AgentAnalysis, InsightResult
from app.agent.service import (compact_profile, complete_json, event_date, execute_plan,
                               make_context, news_evidence, tool_call)
from app.agent.state import AgentLimits, AgentState, Plan
from app.agent.tools import validate_profile
from app.agent.verifier import verify_result


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
    payload = {"profile": state.profile, "news": state.news, "warnings": state.warnings,
               "previous_queries": state.queries,
               "instruction": "Составь короткие тематические запросы. При повторном поиске измени формулировку; отсутствие позиции банка допустимо."}
    plan = await complete_json(runtime.context.tools, state, runtime.context.limits, stage, payload, Plan)
    state.record("plan", f"Сформирован {stage}; банковских запросов: {len(plan.search_queries)}")
    return {"plan": plan}


async def find_evidence(data: Workflow, runtime: Runtime[GraphContext]):
    state = data["agent"]
    await execute_plan(data["plan"], state, runtime.context.tools, runtime.context.limits, event_date(state.news))
    return {"agent": state}


async def assess_evidence(data: Workflow):
    # This is a conservative availability check, not a semantic proof of a claim.
    state = data["agent"]
    enough = any(e["source"]["type"] == "bank" for e in state.evidence.values())
    state.record("verify", "Есть банковские доказательства" if enough else "Банковских доказательств недостаточно",
                 status="ok" if enough else "insufficient_evidence")
    return {"enough": enough}


async def after_assessment(data: Workflow, runtime: Runtime[GraphContext]):
    state, limits = data["agent"], runtime.context.limits
    if (not data["enough"] and data.get("replans", 0) == 0
            and state.llm_calls < limits.max_llm_calls - 1 and state.tool_calls < limits.max_tool_calls):
        return "refine_search"
    return "generate_analysis"


async def refine_search(data: Workflow):
    return {"replans": data.get("replans", 0) + 1}


async def generate_analysis(data: Workflow, runtime: Runtime[GraphContext]):
    state, tools = data["agent"], runtime.context.tools
    context = make_context(state, tools, event_date(state.news))
    try:
        result = await complete_json(tools, state, runtime.context.limits,
            "repair" if data.get("repairs", 0) else "analyze",
            {**context, "warnings": state.warnings, "verification_error": data.get("error")}, InsightResult)
        state.record("execute", "Получен структурированный анализ; проверяем доказательства")
        return {"context": context, "result": result, "error": None}
    except EvidenceError as exc:
        return {"context": context, "result": None, "error": str(exc)}


async def verify_analysis(data: Workflow):
    error = data.get("error")
    if not error:
        try:
            verify_result(data["result"], data["context"])
        except EvidenceError as exc:
            error = str(exc)
    if error:
        data["agent"].record("verify", error, status="insufficient_evidence")
    return {"error": error}


async def after_verification(data: Workflow, runtime: Runtime[GraphContext]):
    if not data.get("error"):
        return "finish"
    if data.get("repairs", 0) == 0 and data["agent"].llm_calls < runtime.context.limits.max_llm_calls:
        return "repair_analysis"
    raise EvidenceError(data["error"])


async def repair_analysis(data: Workflow):
    return {"repairs": data.get("repairs", 0) + 1}


async def finish(data: Workflow):
    state, result = data["agent"], data["result"]
    result.warnings = list(dict.fromkeys([*result.warnings, *state.warnings]))
    if result.bank_position.status == "not_found":
        result.warnings.append("Подтверждённая позиция банка по этому событию не найдена.")
    state.record("verify", "Проверены источники, выдержки, поля компании и даты")
    return {"analysis": AgentAnalysis(result=result, agent_trace=state.trace).model_dump(mode="json")}


@lru_cache(maxsize=1)
def analysis_graph():
    graph = StateGraph(Workflow, context_schema=GraphContext)
    for node in (load_company_and_news, plan_analysis, find_evidence, assess_evidence,
                 refine_search, generate_analysis, verify_analysis, repair_analysis, finish):
        graph.add_node(node.__name__, node)
    graph.add_edge(START, "load_company_and_news")
    graph.add_edge("load_company_and_news", "plan_analysis")
    graph.add_edge("plan_analysis", "find_evidence")
    graph.add_edge("find_evidence", "assess_evidence")
    graph.add_conditional_edges("assess_evidence", after_assessment, ["refine_search", "generate_analysis"])
    graph.add_edge("refine_search", "plan_analysis")
    graph.add_edge("generate_analysis", "verify_analysis")
    graph.add_conditional_edges("verify_analysis", after_verification, ["repair_analysis", "finish"])
    graph.add_edge("repair_analysis", "generate_analysis")
    graph.add_edge("finish", END)
    return graph.compile()
