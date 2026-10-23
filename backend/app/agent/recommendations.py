"""Explainable ranking helper for Igor's news service. No network or model."""
from __future__ import annotations

from datetime import datetime, timezone
import re

from app.rag.search import tokens, topics


def classify_news(news: dict) -> list[str]:
    # Do not trust incoming model-provided tags: derive topics from actual text.
    return sorted(topics(f"{news.get('title', '')} {news.get('body', '')}"))


def rank_news(profile: dict, news_items: list[dict], limit: int = 10) -> list[dict]:
    if not 1 <= limit <= 100:
        raise ValueError("limit должен быть от 1 до 100")
    features = profile.get("features") or {}
    products = features.get("bank_products") or {}
    results, seen = [], set()
    for news in news_items:
        identity = news.get("source_url") or news["id"]
        if identity in seen:
            continue
        seen.add(identity)
        text = f"{news['title']} {news.get('body', '')}"
        tags, score, reasons = set(classify_news(news)), 0, []

        def add(points, field, reason):
            nonlocal score
            score += points
            reasons.append({"company_field": field, "reason": reason})

        industry = features.get("industry_name") or profile.get("industry") or ""
        industry_topics = topics(industry)
        common = set(tokens(industry)) & set(tokens(text))
        if industry_topics & tags or len(common) >= 2:
            field = "features.industry_name" if features.get("industry_name") else "industry"
            add(40, field, "Тема события связана с указанным видом деятельности")
        if "rates" in tags:
            if products.get("loan") is True:
                add(25, "features.bank_products.loan", "Есть кредитный продукт; условия и размер долга неизвестны")
            elif products.get("deposit") is True:
                add(20, "features.bank_products.deposit", "Есть депозитный продукт; тема может быть важна для размещения средств")
        if tags & {"trade", "currency"} and features.get("has_customs_data") is True:
            add(15, "features.has_customs_data", "Есть данные ФТС; направление торговли и валюта расчётов неизвестны")
        region = features.get("region") or profile.get("region")
        if region and str(region).lower() in text.lower():
            add(10, "features.region" if features.get("region") else "region", "В новости упомянут регион компании")
        countries = features.get("customs_countries") or []
        country_forms = {"китай": ("китай", "китая", "китае", "китаю", "китаем")}
        words = set(re.findall(r"[а-яa-z]+", text.lower()))
        if any(any(form in words for form in country_forms.get(str(country).lower(), (str(country).lower(),)))
               or str(country).lower() in text.lower() for country in countries):
            add(10, "features.customs_countries", "В новости упомянута страна из данных ФТС")
        if not score:
            continue  # No arbitrary top news when no personal connection exists.
        results.append({
            "id": str(news["id"]), "title": news["title"],
            "source": news.get("source", "unknown"), "source_url": news.get("source_url"),
            "published_at": news.get("published_at"), "topic_tags": sorted(tags),
            "relevance_score": min(100, score), "relevance_reasons": reasons,
        })

    def published(row):
        try:
            value = datetime.fromisoformat(row["published_at"].replace("Z", "+00:00"))
            return value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()
        except (ValueError, TypeError, AttributeError):
            return 0

    results.sort(key=lambda row: (-row["relevance_score"], -published(row), row["id"]))
    return results[:limit]


async def recommend_news(company_id: str, tools, limit: int = 10) -> dict:
    profile = await tools.get_company_profile(company_id)
    news = await tools.list_news(limit=200)
    return {"company_id": company_id, "items": rank_news(profile, news, limit)}
