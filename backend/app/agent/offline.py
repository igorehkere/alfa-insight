"""Explicit deterministic test double. Never a fallback for a failed LLM."""
import json

from app.agent.schema import LLMReply


class OfflineLLM:
    offline = True
    cache_identity = "offline-extractive-v1"

    def __init__(self):
        self.calls = []

    async def complete(self, messages, *, max_tokens=None):
        request = json.loads(messages[-1]["content"])
        stage, data = request["stage"], request["data"]
        self.calls.append(stage)
        if stage in {"plan", "replan"}:
            from app.agent.recommendations import classify_news
            tags = classify_news(data["news"])
            query = "ключевая ставка кредит" if "rates" in tags else data["news"]["title"][:150]
            if stage == "replan":
                query = "стоимость финансирования" if "rates" in tags else " ".join(data["news"]["title"].split()[:3])
            result = {"search_queries": [query], "need_counterparties": False, "related_news_query": None}
        elif stage in {"analyze", "repair"}:
            news, profile, evidence = data["news"], data["profile"], data["evidence"]
            bank = next((e for e in evidence.values() if e["source"]["type"] == "bank"), None)
            from app.agent.recommendations import rank_news
            ranked = rank_news(profile, [news])
            why = [{"text": reason["reason"], "company_fields": [reason["company_field"]]}
                   for reason in (ranked[0]["relevance_reasons"] if ranked else [])]
            result = {
                "what_happened": [{"text": news["body"], "source_ids": [news["id"]]}],
                "why_relevant": why, "risks": [], "opportunities": [],
                "bank_position": {"status": "found", "text": bank["text"], "source_ids": [bank["source"]["id"]]}
                    if bank else {"status": "not_found", "text": None, "source_ids": []},
                "ai_interpretation": "Офлайн-пример показывает найденные факты и связи с профилем. Персональные риски и возможности требуют анализа языковой моделью.",
                "suggested_actions": ["Уточнить параметры компании, необходимые для оценки влияния события."],
                "sources": [evidence[news["id"]]["source"]] + ([bank["source"]] if bank else []),
                "warnings": ["Офлайн-демонстрация: языковая модель не вызывалась; риски и возможности не генерировались."],
            }
        elif stage == "ask":
            result = {"answer": "Для содержательного ответа на этот вопрос требуется разрешённая языковая модель. В офлайн-режиме доступны сохранённые факты и источники анализа.",
                      "source_ids": [], "warnings": ["Содержательное объяснение в этой демонстрации не формировалось."]}
        else:
            raise ValueError("Неизвестный офлайн-этап")
        return LLMReply(text=json.dumps(result, ensure_ascii=False), model=self.cache_identity,
                        usage=None, elapsed_seconds=0, finish_reason="stop")
