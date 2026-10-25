"""Bounded thematic checks. Heuristics identify gaps, not semantic proof."""
from datetime import date
from decimal import Decimal
import re

from app.agent.recommendations import classify_news, industry_terms


def bank_evidence_gap(news, item, as_of):
    if item.get("reviewed") is not True or item.get("origin") != "bank":
        return "Материал банка не сверён"
    try:
        published = date.fromisoformat(item["published_at"])
    except (KeyError, ValueError, TypeError):
        return "Неизвестна дата банковского материала"
    if published > as_of:
        return "Материал опубликован после выбранного события"
    text = item.get("text", "")
    event = f"{news.get('title', '')} {news.get('body', '')}"
    event_tags = set(classify_news(news))
    bank_tags = set(classify_news({"title": "", "body": text}))
    specific = {"rates", "inflation", "currency", "trade", "construction", "commodities", "labor", "settlements"}
    shared = industry_terms(event) & industry_terms(text)
    if not (event_tags & bank_tags & specific) and len(shared) < 2:
        return "Банковская выдержка не покрывает тему выбранного события"
    return None


def decision_rates(text):
    """Only explicit policy decisions; forecasts are intentionally excluded."""
    if re.search(r"ожида|прогноз|возможно", text.lower()):
        return set()
    return {Decimal(value.replace(",", ".")) for value in re.findall(
        r"(?:сохран\w*|остав\w*|установ\w*)[^.!?]{0,60}?ключев\w*\s+став\w*[^.!?]{0,50}?(\d+(?:[.,]\d+)?)\s*%", text.lower())}
