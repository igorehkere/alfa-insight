"""One LLM client and lazy RAG store per process; repositories own all SQL."""
import asyncio
import os
import inspect
from types import SimpleNamespace

from app.agent.config import LLMConfigurationError
from app.agent.errors import AgentUnavailable
from app.agent.llm_client import LLMClient
from app.agent.service import compact_profile, event_date
from app.agent.tools import ServiceTools
from app.agent.prompts import PROMPT_VERSION
from app.agent.relevance import RelevanceEngine
from app.rag.hybrid import POLICY_VERSION
from app.rag.service_tools import HybridServiceTools


class AgentRuntime:
    def __init__(self, repository, *, llm=None, store=None):
        self.repository, self.llm, self.store = repository, llm, store
        self.analysis_lock = asyncio.Lock()
        self._store_lock = asyncio.Lock()
        self.relevance_engine = RelevanceEngine(llm)

    @classmethod
    def from_env(cls, repository):
        from dotenv import load_dotenv
        from app.agent.config import ENV_PATH
        load_dotenv(ENV_PATH)
        try:
            llm = LLMClient()
        except LLMConfigurationError:
            llm = None  # Existing saved chats still get an explicit general answer.
        return cls(repository, llm=llm)

    async def aclose(self):
        await self.relevance_engine.aclose()
        if self.llm is not None and hasattr(self.llm, "aclose"):
            await self.llm.aclose()

    async def get_store(self):
        async with self._store_lock:
            if self.store is None:
                def build():
                    dsn = os.getenv("RAG_DATABASE_URL") or os.getenv("DATABASE_URL")
                    if not dsn:
                        raise AgentUnavailable("Не задан RAG_DATABASE_URL или DATABASE_URL")
                    mode = os.getenv("RAG_SEARCH_MODE", "fts")
                    if mode == "fts":
                        from app.rag.fts import PgFTSStore
                        return PgFTSStore(dsn)
                    if mode == "hybrid":
                        from app.rag.embeddings import get_encoder
                        from app.rag.postgres import PgVectorStore
                        return PgVectorStore(dsn, get_encoder())
                    raise AgentUnavailable("RAG_SEARCH_MODE должен быть fts или hybrid")
                try:
                    self.store = await asyncio.to_thread(build)
                except (ImportError, OSError, ValueError):
                    raise AgentUnavailable("Подготовьте RAG по app/agent/IGOR_INTEGRATION.md") from None
        return self.store

    async def tools(self, company_id):
        repo = self.repository
        store = await self.get_store()
        result = await asyncio.to_thread(HybridServiceTools, store=store, llm=self.llm, company_id=company_id,
            profile_loader=lambda identifier: asyncio.to_thread(repo.profile, identifier),
            news_loader=lambda identifier: asyncio.to_thread(repo.load_news, identifier),
            news_lister=lambda **kw: asyncio.to_thread(repo.list_news, **kw),
            counterparties_loader=lambda identifier, **kw: asyncio.to_thread(repo.payment_links, identifier, **kw))
        result.relevance_engine = self.relevance_engine
        return result

    def feed_tools(self, company_id):
        """Use the shared compact interpretation cache; no RAG initialization."""
        repo = self.repository
        result = ServiceTools(llm=self.llm, company_id=company_id,
            profile_loader=lambda identifier: asyncio.to_thread(repo.profile, identifier),
            news_loader=lambda identifier: asyncio.to_thread(repo.load_news, identifier),
            news_lister=lambda **kw: asyncio.to_thread(repo.list_news, **kw),
            bank_searcher=None, corpus_version="unused")
        result._pagination_supported = "offset" in inspect.signature(repo.list_news).parameters
        result.relevance_engine = self.relevance_engine
        return result

    def chat_tools(self, company_id):
        # Use only an already initialized local store; never load embeddings for chat.
        result = SimpleNamespace(company_id=company_id, llm=self.llm,
                                 _can_save_chat=callable(getattr(self.repository, "save_chat_turn", None)))
        if self.store is not None:
            async def search(query, *, as_of, limit):
                try:
                    return await asyncio.to_thread(self.store.search, query, limit, as_of=as_of, kind="bank")
                except Exception:
                    # Optional retrieval cannot disclose database/provider exception text.
                    raise AgentUnavailable("Дополнительный поиск временно недоступен") from None
            result.search_chat_evidence = search
        return result

    async def cache_key(self, company_id, news_id, tools):
        profile = await tools.get_company_profile(company_id)
        news = await tools.get_news(news_id)
        version = f"{tools.corpus_version}:{POLICY_VERSION}:{event_date(news)}"
        return self.repository.cache_key(compact_profile(profile), news, version, self.llm.cache_identity, PROMPT_VERSION)
