"""One existing backend plus agent, using no SQL, paid API or embedding model."""
import asyncio
import copy
import hashlib
import json
import unittest
from contextlib import asynccontextmanager
from uuid import uuid4
from unittest.mock import patch

import httpx
from fastapi import APIRouter, FastAPI
from fastapi.testclient import TestClient

from app.agent.errors import LLMError, NotFound
from app.agent.integration import install_agent
from app.agent.offline import OfflineLLM
from app.agent.runtime import AgentRuntime
from app.agent.schema import InsightResponse
from app.agent.state import AgentLimits
from app.agent.tools import FIXTURES, ServiceTools, news_from_record
from app.rag.search import JsonBankSearch


class MemoryRepository:
    def __init__(self):
        self.data = json.loads(FIXTURES.read_text())
        self.runs, self.keys, self.chats = {}, {}, {}

    def profile(self, identifier):
        return next((p for p in self.data["companies"] if p["company_id"] == identifier), None)

    def load_news(self, identifier):
        row = next((n for n in self.data["news"] if n["id"] == identifier), None)
        return news_from_record(row) if row else None

    def list_news(self, *, limit=200, offset=0):
        return [news_from_record(n) for n in self.data["news"][offset:offset+limit]]

    def payment_links(self, identifier, **kwargs):
        return {"company_id": identifier, "items": []}

    def cache_key(self, *material):
        return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()

    def cached(self, key):
        return copy.deepcopy(self.keys.get(key))

    def save_run(self, key, company_id, news_id, run):
        public = InsightResponse(insight_id="insight-"+uuid4().hex, company_id=company_id,
                                 news_id=news_id, **run.analysis).model_dump(mode="json")
        self.keys[key] = public
        self.runs[public["insight_id"]] = {**public, "context": run.context, "usage": run.usage}
        return copy.deepcopy(public)

    def load_insight(self, identifier):
        if identifier not in self.runs:
            raise NotFound("Анализ не найден")
        return copy.deepcopy(self.runs[identifier])

    def save_answer(self, identifier, question, response):
        self.chats.setdefault(identifier, []).append({"question": question, "response": response})

    def messages(self, identifier):
        self.load_insight(identifier)
        return copy.deepcopy(self.chats.get(identifier, []))

    def chat_history(self, identifier):
        rows = self.messages(identifier)
        return {"items": rows[-2:], "turn_count": len(rows)}


class BankStore:
    def __init__(self):
        self.bank = JsonBankSearch()
        self.version = self.bank.version

    def search(self, query, limit, *, as_of, kind):
        return self.bank.search(query, limit, as_of=as_of) if kind == "bank" else []


class IntegrationTests(unittest.TestCase):
    def setUp(self):
        self.events, self.repo = [], MemoryRepository()

        @asynccontextmanager
        async def existing_lifespan(app):
            self.events.append("igor-start")
            yield {"existing_state": True}
            self.events.append("igor-stop")

        self.app = FastAPI(lifespan=existing_lifespan)

        @self.app.get("/api/news/catalog")
        def igors_catalog():
            return {"owner": "igor"}

        @self.app.get("/api/news/{news_id}")
        def igors_details(news_id: str):
            return {"owner": "igor", "id": news_id}

        class TestRuntime(AgentRuntime):
            async def aclose(runtime):
                self.events.append("agent-stop")
                await super().aclose()

        self.runtime = TestRuntime(self.repo, llm=OfflineLLM(), store=BankStore())
        install_agent(self.app, repository=self.repo, runtime_factory=lambda repo: self.runtime)
        self.client = TestClient(self.app)
        self.client.__enter__()
        def close():
            self.client.__exit__(None, None, None)
            self.assertEqual(self.events, ["igor-start", "igor-stop", "agent-stop"])
        self.addCleanup(close)

    def analyze(self, **kwargs):
        response = self.client.post("/api/insights/analyze", json={
            "company_id": "DEMO_AUTO", "news_id": "demo-rates", **kwargs})
        self.assertEqual(response.status_code, 200, response.text)
        self.assertNotIn("context", response.json())
        self.assertNotIn("usage", response.json())
        return response.json()

    def test_existing_news_and_lifespan_are_preserved(self):
        self.assertEqual(self.events, ["igor-start"])
        self.assertEqual(self.client.get("/api/news/catalog").json(), {"owner": "igor"})
        self.assertEqual(self.client.get("/api/news/17").json(), {"owner": "igor", "id": "17"})
        self.assertIn("/api/insights/analyze", self.app.openapi()["paths"])
        with self.assertRaises(ValueError):
            install_agent(self.app, repository=self.repo)

    def test_cache_refresh_chat_trace_and_restore(self):
        first = self.analyze()
        calls = len(self.runtime.llm.calls)
        self.assertEqual(self.analyze()["insight_id"], first["insight_id"])
        self.assertEqual(len(self.runtime.llm.calls), calls)
        fresh = self.analyze(force_refresh=True)
        self.assertNotEqual(first["insight_id"], fresh["insight_id"])
        url = "/api/insights/"+first["insight_id"]
        self.assertEqual(self.client.get(url).json(), first)
        self.assertEqual(self.client.get(url+"/trace").json()["items"], first["agent_trace"])
        for _ in range(4):
            response = self.client.post(url+"/ask", json={"question": "Какие условия стоит проверить?"})
            self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(self.client.get(url+"/messages").json()["items"]), 4)
        self.assertEqual(self.client.get("/api/insights/"+fresh["insight_id"]+"/messages").json()["items"], [])

    def test_feed_without_rag_or_llm_and_company_switch(self):
        self.runtime.store = self.runtime.llm = None
        auto = self.client.get("/api/news", params={"company_id": "DEMO_AUTO"})
        build = self.client.get("/api/news", params={"company_id": "DEMO_BUILD"})
        self.assertEqual(auto.status_code, 200, auto.text)
        self.assertEqual(build.status_code, 200, build.text)
        self.assertNotEqual(auto.json()["items"], build.json()["items"])
        self.assertIsNone(self.runtime.store)

    def test_provider_failure_and_saved_chat_fallback(self):
        first = self.analyze()

        class Failed:
            cache_identity = "failed-test-provider"

            async def complete(self, *args, **kwargs):
                raise LLMError("Провайдер недоступен")

        self.runtime.llm = Failed()
        self.assertEqual(self.client.post("/api/insights/analyze", json={
            "company_id": "DEMO_AUTO", "news_id": "demo-rates"}).status_code, 503)
        answer = self.client.post("/api/insights/"+first["insight_id"]+"/ask", json={"question": "Что проверить?"})
        self.assertEqual(answer.status_code, 200, answer.text)
        self.assertTrue(answer.json()["warnings"])

    def test_invalid_input_and_missing_objects(self):
        for payload in ({"company_id": "x"*101, "news_id": "demo-rates"},
                        {"company_id": "DEMO_AUTO", "news_id": "demo-rates", "role": "admin"}):
            self.assertEqual(self.client.post("/api/insights/analyze", json=payload).status_code, 422)
        self.assertEqual(self.client.post("/api/insights/analyze", json={
            "company_id": "missing", "news_id": "demo-rates"}).status_code, 404)
        self.assertEqual(self.client.get("/api/insights/missing").status_code, 404)
        self.assertEqual(self.client.post("/api/insights/missing/ask", json={"question": "?"*2001}).status_code, 422)

    def test_duplicate_urls_are_rejected_before_mounting(self):
        app = FastAPI()
        router = APIRouter(prefix="/api")

        @router.get("/news", include_in_schema=False)
        def existing_feed():
            return {}

        app.include_router(router)
        with self.assertRaisesRegex(ValueError, "/api/news"):
            install_agent(app, repository=self.repo)
        self.assertFalse(getattr(app.state, "agent_installed", False))
        install_agent(app, repository=self.repo, include_feed=False)


class DeadlineTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_and_generation_share_an_isolated_input_snapshot(self):
        counts = {"profile": 0, "news": 0}

        async def profile(identifier):
            counts["profile"] += 1
            return {"company_id": identifier, "industry": "Исходная отрасль"}

        async def news(identifier):
            counts["news"] += 1
            return {"id": identifier, "title": "Исходная новость", "body": "Исходный текст"}

        tools = ServiceTools(llm=None, company_id="x", profile_loader=profile, news_loader=news,
                             news_lister=None, bank_searcher=None, corpus_version="unused")
        first_profile = await tools.get_company_profile("x")
        first_news = await tools.get_news("17")
        first_profile["industry"] = "Изменено вызывающим кодом"
        first_news["body"] = "Изменено вызывающим кодом"
        self.assertEqual((await tools.get_company_profile("x"))["industry"], "Исходная отрасль")
        self.assertEqual((await tools.get_news("17"))["body"], "Исходный текст")
        self.assertEqual(counts, {"profile": 1, "news": 1})

    async def test_default_runtime_fts_does_not_initialize_embeddings(self):
        runtime = AgentRuntime(MemoryRepository(), llm=OfflineLLM())
        with patch.dict("os.environ", {"RAG_SEARCH_MODE": "fts", "RAG_DATABASE_URL": "postgresql://unused/unused"}), \
             patch.dict("sys.modules", {"app.rag.embeddings": None}):
            from app.rag.fts import PgFTSStore
            self.assertIsInstance(await runtime.get_store(), PgFTSStore)

    async def test_summary_flag_survives_repository_and_service_adapters(self):
        item = news_from_record({"id": 17, "title": "Краткая новость", "description": "Описание из RSS"})
        self.assertTrue(item["body_is_summary"])

        async def loader(identifier):
            return item

        tools = ServiceTools(llm=None, company_id="x", profile_loader=None, news_loader=loader,
                             news_lister=None, bank_searcher=None, corpus_version="unused")
        self.assertTrue((await tools.get_news("17"))["body_is_summary"])

    async def request_with(self, runtime):
        app = FastAPI()
        install_agent(app, repository=runtime.repository, runtime_factory=lambda repo: runtime,
                      limits=AgentLimits(deadline_seconds=0.02), include_feed=False)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test") as client:
                return await client.post("/api/insights/analyze", json={
                    "company_id": "DEMO_AUTO", "news_id": "demo-rates"})

    async def test_deadline_includes_waiting_for_generation_lock(self):
        runtime = AgentRuntime(MemoryRepository(), llm=OfflineLLM())
        await runtime.analysis_lock.acquire()
        try:
            response = await self.request_with(runtime)
            self.assertEqual(response.status_code, 503, response.text)
            self.assertEqual(runtime.llm.calls, [])
        finally:
            runtime.analysis_lock.release()

    async def test_deadline_includes_rag_initialization(self):
        class Slow(AgentRuntime):
            async def tools(self, identifier):
                await asyncio.sleep(1)

        runtime = Slow(MemoryRepository(), llm=OfflineLLM())
        response = await self.request_with(runtime)
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(runtime.llm.calls, [])
        self.assertEqual(runtime.repository.runs, {})
