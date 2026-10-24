"""Reusable API router and ready-to-run prototype factory; no app.test imports."""
import asyncio
import os
from contextlib import asynccontextmanager
from datetime import datetime

from fastapi import APIRouter, FastAPI, Query, Request
from fastapi.responses import JSONResponse
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, HttpUrl, field_validator
from psycopg import Error as PostgresError
from sqlalchemy.exc import IntegrityError, SQLAlchemyError

from app.agent.errors import AgentError, AgentUnavailable, NotFound
from app.agent.recommendations import recommend_news
from app.agent.runtime import AgentRuntime
from app.agent.schema import AnalyzeRequest, AnswerResponse, AskRequest, InsightResponse, NonEmptyText, TraceResponse
from app.agent.service import answer_question, run_analysis
from app.agent.state import AgentLimits


class ErrorResponse(BaseModel):
    detail: str


class ManualNews(BaseModel):
    title: NonEmptyText = Field(max_length=500)
    body: NonEmptyText = Field(max_length=24000)
    source_url: HttpUrl | None = None
    published_at: datetime | None = None

    @field_validator("published_at")
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.tzinfo is None:
            raise ValueError("Дата должна содержать часовой пояс")
        return value


def install_error_handlers(app):
    existing = dict(app.exception_handlers)
    @app.exception_handler(AgentError)
    async def agent_error(request, exc):
        return JSONResponse(status_code=exc.status_code, content={"detail": str(exc)})

    @app.exception_handler(SQLAlchemyError)
    @app.exception_handler(PostgresError)
    async def database_error(request, exc):
        return JSONResponse(status_code=409 if isinstance(exc, IntegrityError) else 503,
                            content={"detail": "Конфликт записи" if isinstance(exc, IntegrityError) else "БД недоступна или не подготовлена"})

    # Respect exception policies already installed by the host backend.
    app.exception_handlers.update(existing)


def create_router(*, include_news=True, include_feed=None):
    """Use app.state.agent_runtime (AgentRuntime), app.state.agent_limits (optional)."""
    router = APIRouter(responses={code: {"model": ErrorResponse} for code in (404, 409, 503)})
    if include_feed is None:
        include_feed = include_news

    @router.post("/api/insights/analyze", response_model=InsightResponse, tags=["Агент"])
    async def analyze(payload: AnalyzeRequest, request: Request):
        runtime = request.app.state.agent_runtime
        limits = getattr(request.app.state, "agent_limits", AgentLimits())
        if runtime.llm is None:
            raise AgentUnavailable("Проверьте LLM_* в окружении backend")
        try:
            # Includes the queue, RAG initialization, loading and cache lookup.
            async with asyncio.timeout(limits.deadline_seconds):
                async with runtime.analysis_lock:
                    tools = await runtime.tools(payload.company_id)
                    key = await runtime.cache_key(payload.company_id, payload.news_id, tools)
                    if not payload.force_refresh:
                        cached = await asyncio.to_thread(runtime.repository.cached, key)
                        if cached:
                            return cached
                    run = await run_analysis(payload.company_id, payload.news_id, tools, limits=limits)
                    return await asyncio.to_thread(runtime.repository.save_run, key, payload.company_id, payload.news_id, run)
        except TimeoutError:
            raise AgentUnavailable("Истекло общее время подготовки анализа") from None

    @router.get("/api/insights/{insight_id}", response_model=InsightResponse, tags=["Агент"])
    async def insight(insight_id: str, request: Request):
        saved = await asyncio.to_thread(request.app.state.agent_runtime.repository.load_insight, insight_id)
        return {key: value for key, value in saved.items() if key in InsightResponse.model_fields}

    @router.post("/api/insights/{insight_id}/ask", response_model=AnswerResponse, tags=["Агент"])
    async def ask(insight_id: str, payload: AskRequest, request: Request):
        runtime = request.app.state.agent_runtime
        async with runtime.analysis_lock:
            saved = await asyncio.to_thread(runtime.repository.load_insight, insight_id)
            history = await asyncio.to_thread(runtime.repository.chat_history, insight_id)
            answer = await answer_question(saved, payload.question, runtime.chat_tools(saved["company_id"]),
                history=history["items"], turn_count=history["turn_count"],
                limits=getattr(request.app.state, "agent_limits", AgentLimits()))
            await asyncio.to_thread(runtime.repository.save_answer, insight_id, payload.question, answer)
            return answer

    @router.get("/api/insights/{insight_id}/trace", response_model=TraceResponse, tags=["Агент"])
    async def trace(insight_id: str, request: Request):
        saved = await asyncio.to_thread(request.app.state.agent_runtime.repository.load_insight, insight_id)
        return {"items": saved["agent_trace"]}

    @router.get("/api/insights/{insight_id}/messages", tags=["Агент"])
    async def history(insight_id: str, request: Request):
        return {"items": await asyncio.to_thread(request.app.state.agent_runtime.repository.messages, insight_id)}

    if include_feed:
        @router.get("/api/news", tags=["Новости"])
        async def feed(request: Request, company_id: str = Query(min_length=1, max_length=100), limit: int = Query(10, ge=1, le=100)):
            runtime = request.app.state.agent_runtime
            return await recommend_news(company_id, runtime.feed_tools(company_id), limit)

    if include_news:
        @router.get("/api/news/catalog", tags=["Новости"])
        async def catalog(request: Request, limit: int = Query(20, ge=1, le=100), offset: int = Query(0, ge=0)):
            return {"items": await asyncio.to_thread(request.app.state.agent_runtime.repository.list_news, limit=limit, offset=offset)}

        @router.post("/api/news/manual", status_code=201, tags=["Новости"])
        async def manual(payload: ManualNews, request: Request):
            return await asyncio.to_thread(request.app.state.agent_runtime.repository.add_news, payload)

        @router.get("/api/news/{news_id}", tags=["Новости"])
        async def news(news_id: str, request: Request):
            item = await asyncio.to_thread(request.app.state.agent_runtime.repository.load_news, news_id)
            if item is None:
                raise NotFound("Новость не найдена")
            return item
    return router


def create_app():
    """uvicorn app.agent.api:create_app --factory. One backend, the same corporate DB."""
    from app.agent import persistence
    from app.main import app as company_app

    @asynccontextmanager
    async def lifespan(app):
        app.state.agent_runtime = AgentRuntime.from_env(persistence)
        app.state.agent_limits = AgentLimits()
        try:
            yield
        finally:
            await app.state.agent_runtime.aclose()

    app = FastAPI(title="Alfa Business Insights", lifespan=lifespan)
    origins = [value.strip() for value in os.getenv("CORS_ORIGINS", "").split(",") if value.strip()]
    if origins:
        app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST"],
                           allow_headers=["Content-Type", "Accept"])
    # Reuse existing company handlers and their own dependencies unchanged.
    app.router.routes.extend(route for route in company_app.routes if getattr(route, "path", "").startswith("/api/companies"))
    app.include_router(create_router())
    install_error_handlers(app)

    @app.get("/health")
    async def health():
        runtime = app.state.agent_runtime
        await asyncio.to_thread(persistence.counts)
        return {"status": "ok", "llm_ready": runtime.llm is not None,
                "llm_endpoint": getattr(runtime.llm, "active_endpoint", None)}
    return app
