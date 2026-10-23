"""Deterministic evidence checks, not a claim of complete semantic verification."""
from __future__ import annotations

import re
from datetime import date

from app.agent.errors import EvidenceError
from app.agent.schema import AnswerResponse, InsightResult


def normalized(text: str) -> str:
    return " ".join(text.split())


def field_value(profile: dict, path: str):
    current = profile
    for part in path.split("."):
        if not isinstance(current, dict) or part not in current:
            raise EvidenceError("Указано несуществующее поле компании")
        current = current[part]
    if current is None or current == "" or current == [] or current == {}:
        raise EvidenceError("Пустое поле компании не подтверждает вывод")
    return current


def numbers(text: str) -> set[str]:
    return set(re.findall(r"\d+(?:[.,]\d+)?", text.replace(",", ".")))


def verify_result(result: InsightResult, context: dict) -> None:
    registry, profile = context["evidence"], context["profile"]
    if not result.what_happened:
        raise EvidenceError("Нет подтверждённых фактов новости")
    primary_id = context["news_id"]
    if not any(primary_id in item.source_ids and
               normalized(item.text) in normalized(registry[primary_id]["text"])
               for item in result.what_happened):
        raise EvidenceError("Анализ должен содержать факт именно выбранной новости")
    used = set()
    for source in result.sources:
        known = registry.get(source.id)
        if not known or source.model_dump() != known["source"]:
            raise EvidenceError("Источник не был получен инструментами или его метаданные подменены")
    for item in result.what_happened:
        if len(normalized(item.text)) < 20:
            raise EvidenceError("Факт слишком короткий для проверяемой выдержки")
        used.update(item.source_ids)
        evidence = [registry[s] for s in item.source_ids]
        if any(e["source"]["type"] != "news" for e in evidence):
            raise EvidenceError("Факт события должен ссылаться на новость")
        if not any(normalized(item.text) in normalized(e["text"]) for e in evidence):
            raise EvidenceError("Факт должен быть точной выдержкой из полученной новости")
    for item in result.why_relevant:
        for path in item.company_fields:
            field_value(profile, path)
    for item in [*result.risks, *result.opportunities]:
        used.update(item.source_ids)
        if item.certainty not in {"possible", "conditional"}:
            raise EvidenceError("Последствие должно быть условным, а не установленным фактом")
        support = " ".join(registry[s]["text"] for s in item.source_ids)
        if not numbers(item.text) <= numbers(support):
            raise EvidenceError("Числа в риске или возможности не подтверждены источниками")
    bank = result.bank_position
    if bank.status == "found":
        if len(normalized(bank.text)) < 40:
            raise EvidenceError("Банковская выдержка слишком короткая для содержательной оценки")
        used.update(bank.source_ids)
        evidence = [registry[s] for s in bank.source_ids]
        if not all(e.get("reviewed") is True and e.get("origin") == "bank" for e in evidence):
            raise EvidenceError("Позиция банка требует сверенного банковского материала")
        if not any(normalized(bank.text) in normalized(e["text"]) for e in evidence):
            raise EvidenceError("Позиция банка должна быть точной выдержкой из найденного фрагмента")
        event_date = date.fromisoformat(context["as_of"])
        for item in evidence:
            published = date.fromisoformat(item["published_at"])
            if published > event_date or (event_date - published).days > 365:
                raise EvidenceError("Дата банковского материала не подходит для текущей позиции")
    if used - {s.id for s in result.sources}:
        raise EvidenceError("Не все использованные источники доступны в ответе")
    # IDs are not enough: assert every referenced record is actually in the registry.
    if used - registry.keys():
        raise EvidenceError("Неизвестное доказательство")


def verify_answer(answer: AnswerResponse, context: dict) -> None:
    if set(answer.source_ids) - context["evidence"].keys():
        raise EvidenceError("Чат сослался на источник вне сохранённого контекста")
    if not answer.source_ids and not answer.warnings:
        raise EvidenceError("Ответ без источников должен явно сообщать об ограничении")
    support = " ".join(context["evidence"][s]["text"] for s in answer.source_ids)
    if not numbers(answer.answer) <= numbers(support):
        raise EvidenceError("Числа в ответе чата не подтверждены процитированными источниками")
