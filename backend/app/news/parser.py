"""Получение и нормализация новостей из RSS."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from time import struct_time
from urllib.parse import urlparse

import feedparser
import httpx
from bs4 import BeautifulSoup


# RSS-адреса
RSS_SOURCES: dict[str, tuple[str, str]] = {
    # Банк России
    "news": (
        "Банк России — новости",
        "https://www.cbr.ru/rss/eventrss",
    ),
    "press": (
        "Банк России — пресс-релизы",
        "https://www.cbr.ru/rss/RssPress",
    ),
    # Коммерсантъ
    "kommersant_business": (
        "Коммерсантъ — бизнес",
        "https://www.kommersant.ru/rss/section-business.xml",
    ),
    "kommersant_economics": (
        "Коммерсантъ — экономика",
        "https://www.kommersant.ru/rss/section-economics.xml",
    ),
    # Ведомости — финансы
    "vedomosti_finance": (
        "Ведомости — финансы",
        "https://www.vedomosti.ru/rss/rubric/finance",
    ),
    # Ведомости — торговля и услуги
    "vedomosti_retail": (
        "Ведомости — торговля и услуги",
        "https://www.vedomosti.ru/rss/rubric/business/retail",
    ),
    # Ведомости — промышленность
    "vedomosti_industry": (
        "Ведомости — промышленность",
        "https://www.vedomosti.ru/rss/rubric/business/industry",
    ),
    # Ведомости — сельское хозяйство
    "vedomosti_agriculture": (
        "Ведомости — агропромышленный комплекс",
        "https://www.vedomosti.ru/rss/rubric/business/agriculture",
    ),
    # Ведомости — транспорт
    "vedomosti_transport": (
        "Ведомости — транспорт",
        "https://www.vedomosti.ru/rss/rubric/business/transport",
    ),
    # Ведомости — налоги
    "vedomosti_taxes": (
        "Ведомости — налоги и сборы",
        "https://www.vedomosti.ru/rss/rubric/economics/taxes",
    ),
    # Ведомости — IT
    "vedomosti_it": (
        "Ведомости — IT-бизнес",
        "https://www.vedomosti.ru/rss/rubric/technology/it_business",
    ),
    # Ведомости — телекоммуникации
    "vedomosti_telecom": (
        "Ведомости — телекоммуникации",
        "https://www.vedomosti.ru/rss/rubric/technology/telecom",
    ),
    # Ведомости — недвижимость
    "vedomosti_realty": (
        "Ведомости — коммерческая недвижимость",
        "https://www.vedomosti.ru/rss/rubric/realty/commercial_property",
    ),
}


@dataclass(frozen=True, slots=True)
class ParsedNews:
    title: str
    description: str | None
    content: str | None
    source_url: str
    source_name: str
    published_at: datetime | None


def clean_text(value: str | None) -> str | None:
    """Убирает HTML-разметку RSS."""
    if not value:
        return None
    text = BeautifulSoup(value, "html.parser").get_text(" ", strip=True)
    text = " ".join(text.split())
    return text or None


def _timestamp_utc(value: struct_time | None) -> datetime | None:
    if value is None:
        return None

    import calendar
    return datetime.fromtimestamp(calendar.timegm(value), tz=timezone.utc)


def parse_rss(content: bytes, source_name: str) -> list[ParsedNews]:
    """Преобразует RSS XML в список новостей"""
    feed = feedparser.parse(content)
    if feed.bozo and not feed.entries:
        raise ValueError(f"Не удалось разобрать RSS источника {source_name}: {feed.bozo_exception}")

    result: list[ParsedNews] = []
    seen_urls: set[str] = set()

    for entry in feed.entries:
        title = clean_text(entry.get("title"))
        url = (entry.get("link") or "").strip()
        parsed = urlparse(url)

        if not title or parsed.scheme not in {"http", "https"} or not parsed.netloc:
            continue
        if url in seen_urls:
            continue
        seen_urls.add(url)

        description = clean_text(entry.get("summary") or entry.get("description"))
        content_entries = entry.get("content") or []
        full_content = (
            clean_text(content_entries[0].get("value"))
            if content_entries else None
        )

        published_at = _timestamp_utc(
            entry.get("published_parsed") or entry.get("updated_parsed")
        )

        result.append(
            ParsedNews(
                title=title,
                description=description,
                content=full_content,
                source_url=url,
                source_name=source_name,
                published_at=published_at,
            )
        )

    return result


def fetch_rss(url: str, source_name: str) -> list[ParsedNews]:
    """Один HTTP-запрос к RSS"""
    response = httpx.get(
        url,
        timeout=20,
        follow_redirects=True,
        headers={
            "User-Agent": "AlfaBusinessInsightsHackathon/0.1 (RSS reader)",
            "Accept": "application/rss+xml, application/xml, text/xml, */*;q=0.8",
        },
    )
    response.raise_for_status()
    return parse_rss(response.content, source_name)