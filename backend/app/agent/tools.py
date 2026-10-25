"""Read-only adapters. No import of main.py, database, .env or HTTP self-calls."""
from __future__ import annotations

import copy
import json
from collections.abc import Awaitable, Callable
from datetime import date
from pathlib import Path
from typing import Protocol

from app.agent.errors import AmbiguousProfile, InvalidInput, NotFound
from app.rag.search import JsonBankSearch, tokens, topics

FIXTURES = Path(__file__).with_name("data") / "fixtures.json"


class AgentTools(Protocol):
    llm: object
    corpus_version: str

    async def get_company_profile(self, company_id: str) -> dict: ...
    async def get_news(self, news_id: str) -> dict: ...
    async def list_news(self, limit: int = 200) -> list[dict]: ...
    async def get_company_counterparties(self, company_id: str, direction: str) -> dict: ...
    async def search_bank_analytics(self, query: str, limit: int, *, as_of: date) -> list[dict]: ...
    async def search_related_news(self, query: str, limit: int) -> list[dict]: ...


def news_from_record(record) -> dict:
    """Accept Igor's ORM News or an equivalent mapping, without importing ORM."""
    def get(key, default=None):
        return record.get(key, default) if isinstance(record, dict) else getattr(record, key, default)

    identifier, title = get("id"), get("title")
    if identifier is None or not str(identifier).strip() or not isinstance(title, str) or not title.strip():
        raise InvalidInput("У новости нет ID или заголовка")
    body = get("body") or get("content") or get("description") or ""
    if not isinstance(body, str):
        raise InvalidInput("Текст новости должен быть строкой")
    published = get("published_at")
    return {"id": str(identifier), "title": title, "body": body[:24000],
            "topic_tags": sorted(topics(title + " " + body)),
            "source": get("source") or get("source_name") or "unknown",
            "source_url": get("source_url"),
            "published_at": published.isoformat() if hasattr(published, "isoformat") else published,
            "provenance": get("provenance", "rss"),
            "body_is_summary": bool(get("body_is_summary", not bool(get("body") or get("content"))))}


def validate_profile(profile: dict, company_id: str) -> dict:
    if not profile:
        raise NotFound("Компания не найдена")
    if not isinstance(profile, dict):
        raise InvalidInput("Адаптер профиля должен вернуть словарь")
    if profile.get("company_id") != company_id:
        raise InvalidInput("Адаптер вернул другую компанию")
    if profile.get("is_ambiguous"):
        raise AmbiguousProfile("Профиль компании неоднозначен")
    return copy.deepcopy(profile)


class JsonTools:
    """Explicit test adapter. The caller must inject an offline or real model."""
    def __init__(self, llm, *, company_id: str, fixtures: Path = FIXTURES,
                 bank_search: JsonBankSearch | None = None):
        self.llm, self.company_id = llm, company_id
        self.data = json.loads(Path(fixtures).read_text(encoding="utf-8"))
        self.bank_search = bank_search or JsonBankSearch()
        self.corpus_version = self.bank_search.version

    def _scope(self, company_id):
        if company_id != self.company_id:
            raise InvalidInput("Запрещена смена компании внутри инструмента")

    async def get_company_profile(self, company_id):
        self._scope(company_id)
        found = next((p for p in self.data["companies"] if p["company_id"] == company_id), None)
        return validate_profile(found, company_id)

    async def get_news(self, news_id):
        found = next((n for n in self.data["news"] if str(n["id"]) == news_id), None)
        if found is None:
            raise NotFound("Новость не найдена")
        return news_from_record(found)

    async def list_news(self, limit=200):
        if not 1 <= limit <= 200:
            raise InvalidInput("Превышен лимит новостей")
        return [news_from_record(n) for n in self.data["news"][:limit]]

    async def get_company_counterparties(self, company_id, direction="outgoing"):
        self._scope(company_id)
        if direction not in {"incoming", "outgoing"}:
            raise InvalidInput("Неизвестное направление платежей")
        return {"company_id": company_id, "direction": direction,
                "items": copy.deepcopy(self.data.get("counterparties", {}).get(company_id, {}).get(direction, []))[:10]}

    async def search_bank_analytics(self, query, limit=5, *, as_of):
        return self.bank_search.search(query, limit, as_of=as_of)

    async def search_related_news(self, query, limit=3):
        if not query.strip() or len(query) > 500 or not 1 <= limit <= 5:
            raise InvalidInput("Неверный запрос связанных новостей")
        terms = set(tokens(query))
        items = await self.list_news()
        return [item for item in items if terms & set(tokens(item["title"] + " " + item["body"]))][:limit]


class ServiceTools:
    """Igor injects async service functions; sessions stay inside those functions.

    Sync SQLAlchemy functions should be wrapped in a worker thread with their own
    short-lived Session, not a Session shared across concurrent async calls.
    """
    def __init__(self, *, llm, company_id: str, profile_loader: Callable[..., Awaitable],
                 news_loader: Callable[..., Awaitable], news_lister: Callable[..., Awaitable],
                 bank_searcher: Callable[..., Awaitable], corpus_version: str,
                 counterparties_loader: Callable[..., Awaitable] | None = None):
        self.llm, self.company_id, self.corpus_version = llm, company_id, corpus_version
        self.profile_loader, self.news_loader = profile_loader, news_loader
        self.news_lister, self.bank_searcher = news_lister, bank_searcher
        self.counterparties_loader = counterparties_loader
        self._profile = None
        self._news = {}

    async def get_company_profile(self, company_id):
        if company_id != self.company_id:
            raise InvalidInput("Компания не входит в разрешённый контекст")
        if self._profile is None:
            self._profile = validate_profile(await self.profile_loader(company_id), company_id)
        return copy.deepcopy(self._profile)

    async def get_news(self, news_id):
        if news_id in self._news:
            return copy.deepcopy(self._news[news_id])
        record = await self.news_loader(news_id)
        if record is None:
            raise NotFound("Новость не найдена")
        item = news_from_record(record)
        if item["id"] != news_id:
            raise InvalidInput("Адаптер вернул другую новость")
        # Cache keys and the generated analysis must use the same input snapshot.
        self._news[news_id] = copy.deepcopy(item)
        return item

    async def list_news(self, limit=200):
        if not 1 <= limit <= 200:
            raise InvalidInput("Превышен лимит новостей")
        return [news_from_record(n) for n in (await self.news_lister(limit=limit))[:limit]]

    async def list_news_page(self, *, limit=200, offset=0):
        import inspect
        if not 1 <= limit <= 200 or offset not in {0, 200, 400}:
            raise InvalidInput("Недопустимая страница новостей")
        supported = getattr(self, "_pagination_supported", None)
        if supported is None:
            supported = "offset" in inspect.signature(self.news_lister).parameters
        if not supported:
            return await self.list_news(limit) if offset == 0 else []
        return [news_from_record(n) for n in (await self.news_lister(limit=limit, offset=offset))[:limit]]

    async def get_company_counterparties(self, company_id, direction="outgoing"):
        if company_id != self.company_id or direction not in {"incoming", "outgoing"}:
            raise InvalidInput("Недопустимый запрос платёжных связей")
        if self.counterparties_loader is None:
            return {"company_id": company_id, "direction": direction, "items": [], "unavailable": True}
        return await self.counterparties_loader(company_id, direction=direction, limit=10)

    async def search_bank_analytics(self, query, limit=5, *, as_of):
        if not query.strip() or len(query) > 500 or not 1 <= limit <= 5:
            raise InvalidInput("Недопустимый запрос банковского поиска")
        return (await self.bank_searcher(query, limit=limit, as_of=as_of))[:limit]

    async def search_related_news(self, query, limit=3):
        if not query.strip() or len(query) > 500 or not 1 <= limit <= 5:
            raise InvalidInput("Недопустимый запрос связанных новостей")
        terms = set(tokens(query))
        return [n for n in await self.list_news() if terms & set(tokens(n["title"] + " " + n["body"]))][:limit]
