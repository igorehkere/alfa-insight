"""Agent-owned tables in a disposable PostgreSQL; no corporate data required."""
import asyncio
import os
import unittest
from unittest.mock import patch
from urllib.parse import urlparse

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.orm import sessionmaker

from app.agent.errors import InvalidInput
from app.agent.offline import OfflineLLM
from app.agent.service import run_analysis
from app.agent.tools import JsonTools

DSN = os.getenv("DIMA_TEST_FTS_DATABASE_URL")


@unittest.skipUnless(DSN, "Set DIMA_TEST_FTS_DATABASE_URL to disposable dima_rag_test")
class PersistenceTests(unittest.TestCase):
    def setUp(self):
        if urlparse(DSN).path != "/dima_rag_test":
            raise ValueError("Persistence tests require a disposable database named dima_rag_test")
        environment = patch.dict(os.environ, {"DATABASE_URL": DSN})
        environment.start()
        self.addCleanup(environment.stop)
        from app.agent import persistence
        self.repo = persistence
        self.engine = create_engine(DSN.replace("postgresql://", "postgresql+psycopg://", 1))
        self.addCleanup(self.engine.dispose)
        self.repo.PersistenceBase.metadata.drop_all(self.engine)
        self.patch_engine = patch("app.connect.engine", self.engine)
        self.patch_sessions = patch.object(self.repo, "SessionLocal", sessionmaker(bind=self.engine))
        self.patch_engine.start()
        self.addCleanup(self.patch_engine.stop)
        self.patch_sessions.start()
        self.addCleanup(self.patch_sessions.stop)

    def test_initialize_only_owns_namespaced_tables_and_is_repeatable(self):
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE IF NOT EXISTS insights (igor_marker text)"))
            connection.execute(text("CREATE TABLE IF NOT EXISTS chat_messages (igor_marker text)"))
        self.repo.initialize()
        self.repo.initialize()
        inspector = inspect(self.engine)
        for name in ["insights", "chat_messages"]:
            self.assertEqual([c["name"] for c in inspector.get_columns(name)], ["igor_marker"])
        self.assertFalse(inspector.has_table("news_items"))
        self.assertTrue(inspector.has_table("dima_agent_insights"))
        self.assertTrue(inspector.has_table("dima_agent_chat_messages"))

    def test_incompatible_agent_schema_fails_without_creating_more_tables(self):
        with self.engine.begin() as connection:
            connection.execute(text("CREATE TABLE dima_agent_insights (id text PRIMARY KEY)"))
        with self.assertRaises(InvalidInput):
            self.repo.initialize()
        self.assertFalse(inspect(self.engine).has_table("dima_agent_chat_messages"))

    def test_saved_context_cache_and_chat_history_survive_new_sessions(self):
        self.repo.initialize()
        model = OfflineLLM()
        run = asyncio.run(run_analysis("DEMO_AUTO", "demo-rates", JsonTools(model, company_id="DEMO_AUTO")))
        first = self.repo.save_run("key", "DEMO_AUTO", "demo-rates", run)
        second = self.repo.save_run("key", "DEMO_AUTO", "demo-rates", run)
        self.assertNotEqual(first["insight_id"], second["insight_id"])
        self.assertEqual(self.repo.cached("key"), second)
        saved = self.repo.load_insight(first["insight_id"])
        self.assertEqual(saved["context"], run.context)
        self.assertNotIn("context", first)
        for index in range(4):
            self.repo.save_answer(first["insight_id"], f"Вопрос {index}", {
                "answer": "Проверьте условия", "source_ids": [], "warnings": []})
        self.assertEqual(len(self.repo.messages(first["insight_id"])), 4)
        history = self.repo.chat_history(first["insight_id"])
        self.assertEqual(history["turn_count"], 4)
        self.assertEqual([r["question"] for r in history["items"]], ["Вопрос 0", "Вопрос 1", "Вопрос 2", "Вопрос 3"])
        self.assertEqual(self.repo.messages(second["insight_id"]), [])

    def test_compact_memory_and_evidence_are_atomic_scoped_and_keep_public_snapshot(self):
        import copy
        from types import SimpleNamespace
        from app.agent.service import answer_question
        from app.agent.errors import AmbiguousProfile
        self.repo.initialize()
        run = asyncio.run(run_analysis("DEMO_AUTO", "demo-rates", JsonTools(OfflineLLM(), company_id="DEMO_AUTO")))
        public = self.repo.save_run("memory", "DEMO_AUTO", "demo-rates", run)
        identifier = public["insight_id"]
        before = copy.deepcopy(self.repo.load_insight(identifier))
        for index in range(16):
            question = "Расширяем автопарк. Бюджет 10 млн рублей. Все машины заняты." if index == 0 else "Бюджет теперь 8 млн рублей." if index == 6 else "Что сравнить?"
            saved = self.repo.load_insight(identifier)
            history = self.repo.chat_history(identifier)
            tools = SimpleNamespace(company_id="DEMO_AUTO", llm=None, _can_save_chat=True)
            response = asyncio.run(answer_question(saved, question, tools, history=history["items"], turn_count=history["turn_count"]))
            self.repo.save_chat_turn(identifier, "DEMO_AUTO", "demo-rates", question, response, tools._chat_update)
        after = self.repo.load_insight(identifier)
        self.assertEqual(after["context"]["_chat"]["memory"]["facts"]["budget"]["value"], "8000000")
        self.assertEqual(after["context"]["evidence"], before["context"]["evidence"])
        self.assertEqual(self.repo.cached("memory"), public)
        self.assertEqual(len(self.repo.chat_history(identifier)["items"]), 12)
        self.assertEqual(len(self.repo.messages(identifier)), 16)
        with self.assertRaises(InvalidInput):
            self.repo.save_chat_turn(identifier, "OTHER", "demo-rates", "?", response, {"turn_count": 16})
        with self.assertRaises(AmbiguousProfile):
            self.repo.save_chat_turn(identifier, "DEMO_AUTO", "demo-rates", "?", response, {"turn_count": 15})
        self.assertEqual(len(self.repo.messages(identifier)), 16)
        from concurrent.futures import ThreadPoolExecutor
        from threading import Barrier
        barrier = Barrier(2)
        internal = {**tools._chat_update, 'turn_count': 16}
        def concurrent_write():
            barrier.wait(timeout=5)
            try:
                self.repo.save_chat_turn(identifier, 'DEMO_AUTO', 'demo-rates', '?', response, internal)
                return 'saved'
            except AmbiguousProfile:
                return 'stale'
        with ThreadPoolExecutor(max_workers=2) as executor:
            futures = [executor.submit(concurrent_write) for _ in range(2)]
            self.assertEqual(sorted(f.result(timeout=10) for f in futures), ['saved', 'stale'])
        self.assertEqual(len(self.repo.messages(identifier)), 17)

    def test_unified_backend_with_real_sql_and_fts_and_synthetic_data(self):
        from datetime import datetime, timezone
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.connect import get_session
        from app.main import app as original
        from app.models import Base, Company, News
        from app.agent.integration import install_agent
        from app.agent.runtime import AgentRuntime
        from app.rag.fts import PgFTSStore
        from app.rag.records import bank_records, TextSplitter
        from app.rag.search import read_corpus
        from app.rag.tests.test_fts import CORPUS

        # These are synthetic rows in the guarded disposable DB, never real data.
        Base.metadata.create_all(self.engine)
        self.addCleanup(Base.metadata.drop_all, self.engine)
        self.repo.initialize()
        with self.repo.SessionLocal.begin() as session:
            for identifier, industry, loan in [("SYN_AUTO", "Торговля автозапчастями", "1"),
                                                ("SYN_BUILD", "Строительство", "0")]:
                session.add(Company(company_id=identifier, industry_name=industry, region="Москва",
                    source_row_count=1, is_ambiguous=False,
                    profile={"MAINOKVED_NAME": industry, "CL_PRODUCT_LOA_MARK": loan}))
            article = News(title="Учебная новость о ставке",
                content="Учебный сценарий: ключевая ставка влияет на стоимость финансирования компаний.",
                source_name="synthetic_fixture", source_url="https://test.invalid/news/sql-synthetic",
                published_at=datetime(2026,10,20,tzinfo=timezone.utc))
            session.add(article)
            session.flush()
            news_id = str(article.id)
        PgFTSStore.initialize(DSN)
        store = PgFTSStore(DSN)
        store.upsert(bank_records(read_corpus(CORPUS), TextSplitter()), kind="bank")
        app = FastAPI()
        app.router.routes.extend(original.routes)

        def session_dependency():
            with self.repo.SessionLocal() as session:
                yield session

        app.dependency_overrides[get_session] = session_dependency
        runtime = AgentRuntime(self.repo, llm=OfflineLLM(), store=store)
        install_agent(app, repository=self.repo, runtime_factory=lambda repo: runtime)
        with TestClient(app) as client:
            self.assertEqual(client.get("/api/news/catalog").json()["items"][0]["id"], int(news_id))
            self.assertEqual(client.get("/api/news/"+news_id).status_code, 200)
            auto = client.get("/api/news", params={"company_id":"SYN_AUTO"})
            build = client.get("/api/news", params={"company_id":"SYN_BUILD"})
            self.assertTrue(auto.json()["items"])
            self.assertEqual(build.json()["items"], [])
            request = {"company_id":"SYN_AUTO", "news_id":news_id}
            response = client.post("/api/insights/analyze", json=request)
            self.assertEqual(response.status_code, 200, response.text)
            saved = response.json()
            self.assertEqual(saved["result"]["bank_position"]["status"], "found")
            self.assertEqual(client.post("/api/insights/analyze", json=request).json()["insight_id"], saved["insight_id"])
            url = "/api/insights/"+saved["insight_id"]
            self.assertEqual(client.get(url).json(), saved)
            self.assertTrue(client.get(url+"/trace").json()["items"])
            self.assertEqual(client.post(url+"/ask", json={"question":"Что проверить?"}).status_code, 200)
            self.assertEqual(len(client.get(url+"/messages").json()["items"]), 1)
