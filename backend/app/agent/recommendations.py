"""Explainable ranking helper for Igor's news service. No network or model."""
from __future__ import annotations

from datetime import datetime, timezone
import re

from app.rag.search import topics


# Feed-specific vocabulary: shared RAG tokenization remains unchanged.
GENERIC = {"деятел", "управл", "услуг", "торгов", "рознич", "оптов", "проч", "собств", "аренд", "органи", "област", "компан", "предпр", "вспомо", "специа"}
INDUSTRIES = {
    "auto": (("45", "29"), ("автомоб", "автозапчаст", "автодетал")),
    "construction": (("41", "42", "43"), ("строител", "стройматериал")),
    "property": (("68",), ("недвижим", "жилищн")),
    "data": (("63.1",), ("датацентр", "дата-центр", "обработк данных", "центры обработки данных")),
    "beverages": (("11", "47.25"), ("напитк", "алкогол", "винодел")),
    "metals": (("24",), ("металлург", "сталь", "сталелит", "сталевар")),
    "agriculture": (("01",), ("сельскохозяй", "аграр", "зерн", "урожай")),
    "transport": (("49", "50", "51", "52"), ("грузоперевоз", "логист", "транспортн")),
}


def industry_terms(text):
    words = re.findall(r"[а-яa-z]+", text.lower().replace("ё", "е"))
    return {word[:6] for word in words if len(word) >= 4 and not any(word.startswith(root) for root in GENERIC)}


def classify_news(news: dict) -> list[str]:
    text = f"{news.get('title', '')} {news.get('body', '')}".lower().replace("ё", "е")
    tags = topics(text) - {"rates", "currency"}
    coin = bool(re.search(r"памятн\w* монет|монет\w* .*номинал|выпуск\w* .*монет", text))
    retail = bool(re.search(r"вклад\w* .*физическ|депозит\w* .*населени|вклад\w* .*населени|средств\w* .*физическ", text))
    if retail:
        tags.add("retail_deposits")
    if re.search(r"ключев\w* ставк|стоимост\w* финансирован|кредитован\w* (?:компани|бизнес|предприят)|корпоративн\w* кредит|ставк\w* по кредит", text):
        tags.add("rates")
    if re.search(r"валютн\w* (?:рын|курс|расчет|огранич|операц)|курс\w* (?:рубл|доллар|евро|юан)|(?:ослаблен|укреплен)\w* рубл", text):
        tags.add("currency")
    if re.search(r"рын\w* труда|заработн\w* плат|зарплат|дефицит\w* кадр", text):
        tags.add("labor")
    if re.search(r"(?:огранич|приостан|нов\w* правил)\w* [^.]*расчет|платежн\w* (?:огранич|систем)|расчетн\w* (?:огранич|счет)", text):
        tags.add("settlements")
    if coin:
        tags = {"commemorative_coins"}
    return sorted(tags)


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
        industry_lower = industry.lower().replace("ё", "е")
        code = str(features.get("industry_code") or profile.get("industry_code") or "")
        lower = text.lower().replace("ё", "е")
        common = industry_terms(industry) & industry_terms(text)
        matched = []
        for name, (codes, roots) in INDUSTRIES.items():
            in_profile = any(re.match(r"^" + re.escape(prefix) + r"(?:\.|$)", code) for prefix in codes)
            in_profile = in_profile or any(root in industry_lower for root in roots)
            if in_profile and any(root in lower for root in roots):
                matched.append(name)
        if "commemorative_coins" not in tags and (matched or len(common) >= 2):
            field = "features.industry_name" if features.get("industry_name") else "industry"
            if not industry and code:
                field = "features.industry_code" if features.get("industry_code") else "industry_code"
            add(40, field, "Событие затрагивает рынок указанного вида деятельности; условия конкретной компании неизвестны")
        if "rates" in tags:
            if products.get("loan") is True:
                add(25, "features.bank_products.loan", "Стоимость финансирования может влиять на кредитный продукт; долг и условия неизвестны")
            elif products.get("leasing") is True:
                add(25, "features.bank_products.leasing", "Стоимость финансирования может влиять на лизинговые платежи; условия договора неизвестны")
            elif products.get("deposit") is True:
                add(20, "features.bank_products.deposit", "Ключевая ставка может влиять на размещение средств; условия депозита неизвестны")
        if "labor" in tags and products.get("payroll") is True:
            add(20, "features.bank_products.payroll", "Изменения оплаты труда могут влиять на зарплатные выплаты; состав персонала неизвестен")
        if "settlements" in tags and products.get("settlement") is True:
            add(20, "features.bank_products.settlement", "Правила расчётов могут влиять на РКО; применимость ограничений требует проверки")
        if tags & {"trade", "currency"} and features.get("has_customs_data") is True:
            add(15, "features.has_customs_data", "Изменения внешней торговли или валютных расчётов могут влиять на операции; направление и валюта неизвестны")
        region = features.get("region") or profile.get("region")
        sentences = re.split(r"[.!?]", lower)
        regional_business = region and any(str(region).lower() in sentence and
            re.search(r"строител|инвестиц|рынок труда|занятост|логист|производств|поддержк\w* бизнеса|огранич\w* [^.]*предприят", sentence)
            and not re.search(r"монетн\w* двор|местонахождени|юридическ\w* адрес", sentence) for sentence in sentences)
        if regional_business and "commemorative_coins" not in tags:
            add(10, "features.region" if features.get("region") else "region", "Событие затрагивает деловую среду региона; участие компании неизвестно")
        countries = features.get("customs_countries") or []
        country_forms = {"китай": ("китай", "китая", "китае", "китаю", "китаем")}
        words = set(re.findall(r"[а-яa-z]+", lower))
        if tags & {"trade", "currency"} and any(
            any(form in words for form in country_forms.get(str(country).lower(), (str(country).lower(),)))
            for country in countries):
            add(10, "features.customs_countries", "Изменения торговли или расчётов касаются страны из ФТС; операции компании требуют уточнения")
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
