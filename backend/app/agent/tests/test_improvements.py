"""Behavioral regressions from suggestions/improvements.md; no live provider."""
import asyncio
import copy
import json
import unittest
from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.chat import ChatDraft, chat_context, verified_answer
from app.agent.errors import EvidenceError
from app.agent.service import answer_question, run_analysis
from app.agent.recommendations import rank_news, classify_news
from app.agent.state import AgentLimits, AgentState
from app.agent.graph import assess_evidence
from app.agent.tools import JsonTools
from app.agent.tests.test_pipeline import MutatingLLM
from app.agent.schema import LLMReply


def saved():
    source = {"id": "103", "type": "news", "title": "Противодействие инсайдерской торговле", "url": None}
    return {"company_id": "U_10002", "news_id": "103",
            "result": {"sources": [source], "ai_interpretation": "Сотрудник обязан получить кредит.",
                       "suggested_actions": ["Проверить внутренние правила"], "warnings": []},
            "context": {"company_id": "U_10002", "news_id": "103", "as_of": "2026-10-25",
                        "profile": {}, "news": {"title": source["title"], "body_is_summary": True},
                        "evidence": {"103": {"source": source, "text": "Сообщение о противодействии инсайдерской торговле.",
                                             "published_at": "2026-10-20"}}}}


class Replies:
    def __init__(self, *answers):
        self.answers, self.calls = list(answers), []

    async def complete(self, messages, **kwargs):
        self.calls.append((json.loads(messages[-1]["content"])["data"], kwargs))
        return LLMReply(text=self.answers.pop(0).model_dump_json(), model="test", elapsed_seconds=0, finish_reason="stop")


class ChatImprovements(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        block = patch("httpx.AsyncClient.send", side_effect=AssertionError("No paid requests"))
        block.start(); self.addCleanup(block.stop)

    async def test_employee_rejections_get_useful_scoped_help(self):
        bad = ChatDraft(answer="Кредит под 3% одобрен.", source_ids=["103"])
        llm = Replies(bad, bad)
        result = await answer_question(saved(), "Я сотрудник. Что делать?", SimpleNamespace(company_id="U_10002", llm=llm))
        self.assertEqual(len(llm.calls), 2)
        self.assertIn("комплаенс", result["answer"])
        self.assertIn("Могу составить", result["answer"])
        self.assertIn("?", result["answer"])
        self.assertNotIn("кредит", result["answer"].lower())
        self.assertNotIn("3%", result["answer"])
        self.assertEqual(result["source_ids"], [])
        self.assertEqual(set(result), {"answer", "source_ids", "warnings"})
        self.assertTrue(llm.calls[1][0]["verification_error"])
        self.assertLessEqual(sum(call[1]["max_tokens"] for call in llm.calls), 4000)
        self.assertIn("suggested_actions", llm.calls[0][0]["analysis_summary"])
        self.assertTrue(any("подтвердить" in w for w in result["warnings"]))

    async def test_followup_uses_user_topic_not_prior_ai(self):
        example = saved(); example["context"]["news"]["title"] = "Новость"
        example["context"]["evidence"] = {}
        tools = SimpleNamespace(company_id="U_10002", llm=None)
        equipment = [{"question": "Нужно новое оборудование", "response": {"answer": "Кредит уже одобрен"}}]
        employee = [{"question": "Я сотрудник, вопрос о рабочих обязанностях", "response": {"answer": "Кредит уже одобрен"}}]
        a = await answer_question(example, "Расскажи подробнее", tools, history=equipment)
        b = await answer_question(example, "Расскажи подробнее", tools, history=employee)
        self.assertIn("оборудование", a["answer"])
        self.assertIn("обязанности", b["answer"])
        self.assertNotIn("одобрен", a["answer"] + b["answer"])
        reset = await answer_question(example, "Расскажи подробнее", tools, history=equipment*3)
        self.assertIn("оборудование", reset["answer"])

    async def test_fallback_reasons_and_available_date(self):
        example = saved(); example["context"]["evidence"] = {}
        result = await answer_question(example, "Что изменилось сегодня?", SimpleNamespace(company_id="U_10002", llm=Replies()))
        self.assertTrue(any("выдержек" in w for w in result["warnings"]))
        self.assertTrue(any("2026-10-25" in w for w in result["warnings"]))
        class Slow:
            async def complete(self, *args, **kwargs):
                await asyncio.sleep(1)
        result = await answer_question(saved(), "Что делать?", SimpleNamespace(company_id="U_10002", llm=Slow()),
                                       limits=AgentLimits(deadline_seconds=0.01))
        self.assertTrue(any("Истекло" in w for w in result["warnings"]))

    def test_exact_id_and_list_markers_are_not_factual_numbers(self):
        example = saved()
        value = ChatDraft(answer="Для вашей компании:\n1. Уточните роль.\n2) Проверьте внутренние правила.", source_ids=["103"])
        self.assertEqual(verified_answer(value, example["context"], "?").answer, value.answer)
        for text in ["Для U_99999 проверьте условия.", "Проверьте 2 документа."]:
            with self.assertRaises(EvidenceError):
                verified_answer(ChatDraft(answer=text, source_ids=["103"]), example["context"], "?")

    def test_equivalent_formats_units_subjects_and_promises(self):
        context = saved()["context"]
        context["evidence"]["103"]["text"] = "Ключевая ставка составляет 14,0%. Сумма составляет 1 млн рублей."
        for text in ["Ключевая ставка составляет 14%.", "Сумма составляет 1 000 000 рублей."]:
            verified_answer(ChatDraft(answer=text, source_ids=["103"]), context, "?")
        for text in ["Ключевая ставка составляет 14 рублей.", "Кредит под 14%.", "Ключевая ставка 14%, кредит под 14%.", "Банк выдаст кредит.", "Вы обязаны оформить кредит."]:
            with self.subTest(text=text), self.assertRaises(EvidenceError):
                verified_answer(ChatDraft(answer=text, source_ids=["103"]), context, "?")

    def test_number_sign_and_time_unit_cannot_be_substituted(self):
        context = saved()["context"]
        context["evidence"]["103"]["text"] = "Изменение составляет -4%. Срок составляет 14 дней."
        verified_answer(ChatDraft(answer="Изменение составляет −4%.", source_ids=["103"]), context, "?")
        for text in ["Изменение составляет 4%.", "Срок составляет 14 месяцев."]:
            with self.assertRaises(EvidenceError):
                verified_answer(ChatDraft(answer=text, source_ids=["103"]), context, "?")

    def test_numeric_company_id_is_only_exempt_as_an_identifier(self):
        context = saved()["context"]
        context["company_id"] = "10002"
        verified_answer(ChatDraft(answer="Для компании 10002 уточните роль.", source_ids=["103"]), context, "?")
        for text in ["Сумма 10002 рублей.", "Ставка 10002%."]:
            with self.assertRaises(EvidenceError):
                verified_answer(ChatDraft(answer=text, source_ids=["103"]), context, "?")

    async def test_topic_change_overrides_the_previous_question(self):
        result = await answer_question(saved(), "Теперь нужно новое оборудование", SimpleNamespace(company_id="U_10002", llm=None),
            history=[{"question": "Я сотрудник, что делать?", "response": {"answer": "Уточните обязанности"}}])
        self.assertIn("оборудование", result["answer"])
        with self.assertRaises(EvidenceError):
            verified_answer(ChatDraft(answer="Возьмите кредит.", source_ids=["103"]), saved()["context"], "Я сотрудник, что делать?")

    def test_context_keeps_late_quote_and_conditions_without_mutation(self):
        example = saved(); original = "Вводное предложение о событии. " * 100
        quote = "Ставка не изменится, если условие не выполнено."
        example["context"]["evidence"]["103"]["text"] = original + quote + " Дата относится к публикации."
        example["result"]["what_happened"] = [{"text": quote, "source_ids": ["103"]}]
        before = copy.deepcopy(example)
        context = chat_context(example)
        self.assertIn(quote, context["evidence"]["103"]["text"])
        self.assertLessEqual(len(context["evidence"]["103"]["text"]), 1800)
        self.assertEqual(example, before)


class FeedImprovements(unittest.TestCase):
    def news(self, body):
        return {"id": "new", "title": body, "body": body}

    def profile(self, industry, **features):
        return {"industry": industry, "features": features}

    def test_diagnostic_false_connections_are_excluded(self):
        for profile, text in [
            (self.profile("Управление недвижимостью за вознаграждение"), "За нарушение правил инсайдерской торговли управляющей компанией"),
            (self.profile("Обработка данных и информационные услуги"), "Информация о финансовых услугах: вклады физических лиц в рублях"),
            (self.profile("Инженерные изыскания и управление деятельностью"), "Прекращение деятельности и отзыв лицензии кредитной организации"),
            (self.profile("Магазин напитков", region="Санкт-Петербург"), "Памятные монеты: Санкт-Петербургский монетный двор, номинал 3 рубля"),
            (self.profile("Торговля", has_customs_data=True), "Объем вкладов физических лиц в рублях вырос"),
        ]:
            with self.subTest(text=text):
                self.assertEqual(rank_news(profile, [self.news(text)]), [])

    def test_real_industry_code_and_product_mechanisms(self):
        cases = [
            (self.profile("", industry_code="45.31"), "Продажи автомобилей замедляются", "features.industry_code"),
            (self.profile("Строительство"), "Развитие строительства", "industry"),
            (self.profile("", bank_products={"leasing": True}), "Стоимость финансирования выросла", "features.bank_products.leasing"),
            (self.profile("", bank_products={"payroll": True}), "Рынок труда: заработная плата выросла", "features.bank_products.payroll"),
            (self.profile("", bank_products={"settlement": True}), "Платежные ограничения затронули расчеты", "features.bank_products.settlement"),
            (self.profile("", has_customs_data=True), "Валютный рынок: курс рубля изменился", "features.has_customs_data"),
        ]
        for profile, text, field in cases:
            with self.subTest(text=text):
                ranked = rank_news(profile, [self.news(text)])
                self.assertTrue(ranked)
                self.assertEqual(ranked[0]["relevance_reasons"][0]["company_field"], field)
                self.assertLessEqual(ranked[0]["relevance_score"], 100)

    def test_region_requires_business_event(self):
        profile = self.profile("", region="Москва")
        self.assertEqual(rank_news(profile, [self.news("Юридический адрес организации Москва")]), [])
        self.assertTrue(rank_news(profile, [self.news("Москва: новая программа поддержки бизнеса")]))


class EvidenceImprovements(unittest.IsolatedAsyncioTestCase):
    async def test_irrelevant_bank_text_replans_and_never_becomes_position(self):
        model = MutatingLLM(lambda stage, value: value.update(search_queries=["ключевая ставка"]) if stage in {"plan", "replan"} else None)
        tools = JsonTools(model, company_id="DEMO_AUTO")
        tools.data["news"].append({"id": "unrelated", "title": "Наблюдение метеорита", "body": "Астрономы сообщили о наблюдении метеорита.", "published_at": "2026-10-20"})
        run = await run_analysis("DEMO_AUTO", "unrelated", tools)
        self.assertEqual(model.calls, ["plan", "replan", "analyze"])
        self.assertEqual(run.analysis["result"]["bank_position"]["status"], "not_found")
        self.assertTrue(any("тему выбранного" in row["details"] for row in run.analysis["agent_trace"]))

    async def test_conflicting_bank_decisions_are_quarantined(self):
        state = AgentState("c", "n", news={"title": "Ключевая ставка", "body": "Ставка сохранена", "published_at": "2026-10-20"})
        for identifier, value in [("a", "14"), ("b", "15")]:
            state.evidence[identifier] = {"source": {"type": "bank"}, "text": f"ЦБ сохранил ключевую ставку на уровне {value}%.",
                                          "published_at": "2026-10-20", "reviewed": True, "origin": "bank"}
        self.assertFalse((await assess_evidence({"agent": state}))["enough"])
        self.assertEqual(state.evidence, {})

    async def test_policy_rate_and_loan_rate_are_not_a_conflict(self):
        state = AgentState("c", "n", news={"title": "Ключевая ставка", "body": "Ставка сохранена", "published_at": "2026-10-20"})
        state.evidence["a"] = {"source": {"type": "bank"},
                               "text": "ЦБ сохранил ключевую ставку на уровне 14%. Банк сохранил ставку по кредиту на уровне 15%.",
                               "published_at": "2026-10-20", "reviewed": True, "origin": "bank"}
        self.assertTrue((await assess_evidence({"agent": state}))["enough"])
        self.assertEqual(len(state.evidence), 1)

    async def test_equivalent_decision_values_are_not_a_conflict(self):
        state = AgentState("c", "n", news={"title": "Ключевая ставка", "body": "Ставка сохранена", "published_at": "2026-10-20"})
        for identifier, value in [("a", "14"), ("b", "14,0")]:
            state.evidence[identifier] = {"source": {"type": "bank"}, "text": f"ЦБ сохранил ключевую ставку на уровне {value}%.",
                                          "published_at": "2026-10-20", "reviewed": True, "origin": "bank"}
        self.assertTrue((await assess_evidence({"agent": state}))["enough"])
        self.assertEqual(len(state.evidence), 2)


class CompatibilityImprovements(unittest.IsolatedAsyncioTestCase):
    async def test_prompt_version_changes_cache_key(self):
        from app.agent.offline import OfflineLLM
        from app.agent.runtime import AgentRuntime
        from app.agent.tests.test_integration import MemoryRepository
        runtime = AgentRuntime(MemoryRepository(), llm=OfflineLLM())
        tools = JsonTools(runtime.llm, company_id="DEMO_AUTO")
        current = await runtime.cache_key("DEMO_AUTO", "demo-rates", tools)
        with patch("app.agent.runtime.PROMPT_VERSION", "dima-langgraph-rag-v3"):
            previous = await runtime.cache_key("DEMO_AUTO", "demo-rates", tools)
        self.assertNotEqual(previous, current)


class HostCompatibilityImprovements(unittest.TestCase):
    def test_existing_middleware_and_error_handler_survive_installation(self):
        from fastapi import FastAPI
        from fastapi.responses import JSONResponse
        from fastapi.testclient import TestClient
        from app.agent.errors import AgentError
        from app.agent.integration import install_agent
        from app.agent.runtime import AgentRuntime
        from app.agent.tests.test_integration import MemoryRepository
        app = FastAPI()
        @app.middleware("http")
        async def existing_middleware(request, call_next):
            response = await call_next(request)
            response.headers["X-Existing"] = "igor"
            return response
        @app.exception_handler(AgentError)
        async def existing_handler(request, exc):
            return JSONResponse(status_code=409, content={"existing": "igor"})
        repo = MemoryRepository()
        runtime = AgentRuntime(repo, llm=None)
        install_agent(app, repository=repo, runtime_factory=lambda repo: runtime, include_feed=False)
        with TestClient(app) as client:
            response = client.get("/api/insights/missing")
            self.assertEqual(response.status_code, 409)
            self.assertEqual(response.json(), {"existing": "igor"})
            self.assertEqual(response.headers["X-Existing"], "igor")
        self.assertNotIn("/api/news", app.openapi()["paths"])
