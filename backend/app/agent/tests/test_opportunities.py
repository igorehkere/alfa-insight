"""Business-policy regressions. All providers are fakes; no paid calls."""
import asyncio
import copy
import json
import unittest
from datetime import date
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from app.agent.calculations import Quantity, calculate, ledger_for
from app.agent.chat import ChatDraft, recent_dialogue, verified_answer
from app.agent.errors import EvidenceError, InvalidInput
from app.agent.language import ensure_human, profile_facts, compact_warnings, result_texts
from app.agent.memory import task_memory
from app.agent.recommendations import rank_news, recommend_news
from app.agent.service import answer_question, run_analysis
from app.agent.state import AgentLimits, AgentState
from app.agent.tests.test_improvements import saved, Replies
from app.agent.tests.test_pipeline import MutatingLLM
from app.agent.tools import JsonTools, ServiceTools
from app.agent.verifier import verify_answer, verify_result
from app.agent.schema import AnswerResponse, InsightResult, LLMReply


class LanguageTests(unittest.IsolatedAsyncioTestCase):
    def test_dictionary_preserves_meaning_without_inventing_unknown_flags(self):
        facts = profile_facts({'industry': 'Торговля оборудованием', 'industry_code': '46.69.9', 'features': {
            'bank_products': {'loan': False, 'leasing': True, 'mystery': True}, 'has_customs_data': True}})
        ensure_human(*facts)
        self.assertTrue(any('других банках' in text for text in facts))
        self.assertTrue(any('лизинг' in text and 'неизвестны' in text for text in facts))
        self.assertFalse(any('mystery' in text for text in facts))

    def test_machine_text_is_rejected_in_each_visible_analysis_field(self):
        bad = ['industry_code 46.69.9', 'features.bank_products.leasing', 'name=true', 'source_ids',
               'bank_position=not_found', '{"amount": 12}', 'SELECT secret FROM companies', 'leasing', 'has_customs_data', 'run_analysis']
        for text in bad:
            with self.subTest(text=text), self.assertRaises(EvidenceError):
                ensure_human(text)
        ensure_human('В профиле указан лизинг; условия договора неизвестны. Стоимость 2,5 млн рублей.')

    async def test_analysis_repairs_technical_prose_but_keeps_machine_metadata(self):
        def mutate(stage, result):
            if stage == 'analyze':
                result['why_relevant'][0]['text'] = 'industry_code 46.69.9'
        model = MutatingLLM(mutate)
        run = await run_analysis('DEMO_AUTO', 'demo-rates', JsonTools(model, company_id='DEMO_AUTO'))
        self.assertEqual(model.calls.count('repair'), 1)
        result = InsightResult.model_validate(run.analysis['result'])
        ensure_human(*result_texts(result), *(row['details'] for row in run.analysis['agent_trace']))
        self.assertTrue(result.why_relevant[0].company_fields)
        self.assertLessEqual(len(model.calls), 4)

    async def test_historical_bank_background_is_dated_and_listed_when_position_missing(self):
        def mutate(stage, result):
            if stage in {'analyze', 'repair'}:
                result['bank_position'] = {'status': 'not_found', 'text': None, 'source_ids': []}
                result['ai_interpretation'] = ('Исторический материал от 2026-09-11: ' +
                    'Мы ожидаем сохранения паузы на заседании 23 октября: С учетом риторики ЦБ РФ мы меняем наш взгляд на динамику ключевой ставки на этот год.')
        model = MutatingLLM(mutate)
        run = await run_analysis('DEMO_AUTO', 'demo-rates', JsonTools(model, company_id='DEMO_AUTO'))
        self.assertEqual(run.analysis['result']['bank_position']['status'], 'not_found')
        self.assertTrue(any(source['type'] == 'bank' for source in run.analysis['result']['sources']))
        self.assertIn('2026-09-11', run.analysis['result']['ai_interpretation'])
        verify_result(InsightResult.model_validate(run.analysis['result']), run.context)

    async def test_old_relevant_report_is_background_and_cannot_be_current_position(self):
        def mutate(stage, result):
            if stage in {'analyze', 'repair'}:
                result['bank_position'] = {'status': 'not_found', 'text': None, 'source_ids': []}
                result['ai_interpretation'] = 'Исторический обзор финансирования от 2021-04-15: условия финансирования компаний зависели от ключевой ставки.'
        tools = JsonTools(MutatingLLM(mutate), company_id='DEMO_AUTO')
        async def old_search(*args, **kwargs):
            return [{'reviewed': True, 'origin': 'bank', 'published_at': '2021-04-15', 'chunk_id': 'old:p1',
                     'document_id': 'old', 'document_title': 'Исторический обзор финансирования', 'page_number': 1,
                     'text': 'Условия финансирования компаний зависели от ключевой ставки.', 'source_url': None}]
        tools.search_bank_analytics = old_search
        run = await run_analysis('DEMO_AUTO', 'demo-rates', tools)
        self.assertFalse(run.context['evidence']['bank:old:p1']['eligible_for_current_position'])
        result = InsightResult.model_validate(run.analysis['result'])
        self.assertEqual(result.bank_position.status, 'not_found')
        self.assertTrue(any(source.type == 'bank' for source in result.sources))
        result.bank_position.status = 'found'
        result.bank_position.text = run.context['evidence']['bank:old:p1']['text']
        result.bank_position.source_ids = ['bank:old:p1']
        with self.assertRaises(EvidenceError):
            verify_result(result, run.context)

    async def test_two_technical_drafts_get_useful_local_help(self):
        bad = ChatDraft(answer='features.bank_products.leasing=true', source_ids=['103'])
        llm = Replies(bad, bad)
        result = await answer_question(saved(), 'Расширяем автопарк. Бюджет 10 млн рублей. Все машины заняты.',
                                       SimpleNamespace(company_id='U_10002', llm=llm))
        self.assertEqual(len(llm.calls), 2)
        self.assertEqual(set(result), {'answer', 'source_ids', 'warnings'})
        ensure_human(result['answer'], *result['warnings'])
        self.assertIn('10000000', result['answer'])
        self.assertIn('прибыльный спрос', result['answer'])
        self.assertTrue(any(word in result['answer'] for word in ('поэтапно', 'поэтапное')))
        self.assertLessEqual(sum(call[1]['max_tokens'] for call in llm.calls), 4000)

    def test_warning_semantic_duplicates(self):
        rows = compact_warnings(['Доступно краткое описание новости.', 'Доступен не полный текст публикации.',
                                 'Дата новости неизвестна.', 'Дата новости неизвестна.'])
        self.assertEqual(len(rows), 2)


class MemoryTests(unittest.IsolatedAsyncioTestCase):
    def test_fourth_seventh_and_twentieth_turn_restore_conditions(self):
        history = [{'question': 'Расширяем автопарк. Бюджет 10 млн рублей. Все машины заняты.', 'response': {'answer': 'Банк одобрит 7%.'}}]
        for index in range(1, 21):
            dialogue, count = recent_dialogue(history)
            memory = task_memory({}, dialogue, 'Что сравнить?', count)
            if index <= 12:
                self.assertEqual(memory['facts']['budget']['value'], '10000000')
            history.append({'question': 'Что сравнить?', 'response': {'answer': 'Необоснованная догадка'}})
        # Compact state continues after the original message leaves the read window.
        initial = task_memory({}, [], history[0]['question'], 0)
        memory = task_memory({'_chat': {'memory': initial}}, *[recent_dialogue(history)[0]], 'Что сравнить?', len(history))
        self.assertEqual(memory['facts']['budget']['value'], '10000000')
        self.assertNotIn('7%', str(memory))

    def test_correction_topic_and_explicit_reset(self):
        initial = task_memory({}, [], 'Нужно оборудование, бюджет 10 млн рублей.', 0)
        corrected = task_memory({'_chat': {'memory': initial}}, [], 'Бюджет теперь 8 млн рублей.', 1)
        self.assertEqual(corrected['facts']['budget']['value'], '8000000')
        changed = task_memory({'_chat': {'memory': corrected}}, [], 'Теперь обсуждаем найм сотрудника.', 2)
        self.assertNotIn('budget', changed['facts'])
        reset = task_memory({'_chat': {'memory': initial}}, [], 'Забудь условия, начнём заново.', 2)
        self.assertFalse(reset['facts'])

    def test_no_assumptions_or_negative_values_become_inputs(self):
        for text in ['Предположим бюджет 10 млн рублей.', 'Бюджет -5 млн рублей.', 'Если бы бюджет был 2 млн рублей.']:
            self.assertNotIn('budget', task_memory({}, [], text, 0)['facts'])
        facts = task_memory({}, [], 'Бюджет 10 млн. рублей, резерв 2 млн. рублей.', 0)['facts']
        self.assertEqual(facts['reserve']['value'], '2000000')

    async def test_same_followup_has_different_task_specific_help_and_refinement(self):
        tools = SimpleNamespace(company_id='U_10002', llm=None)
        texts = []
        for first in ['Нужно оборудование', 'Нужен найм сотрудника', 'Вопрос об обязанностях сотрудника']:
            result = await answer_question(saved(), 'Что делать?', tools, history=[{'question': first, 'response': {'answer': 'Кредит одобрен'}}])
            texts.append(result['answer'])
        self.assertEqual(len(set(texts)), 3)
        self.assertNotIn('кредит', texts[1].lower() + texts[2].lower())
        history = [{'question': 'Расширяем автопарк. Бюджет 10 млн рублей. Все машины заняты.', 'response': {'answer': ''}}]
        a = await answer_question(saved(), 'Что делать?', tools, history=history)
        b = await answer_question(saved(), 'Есть неудовлетворённый спрос, отказываем клиентам. Резерв 2 млн рублей.', tools, history=history)
        self.assertNotEqual(a['answer'], b['answer'])
        self.assertIn('8000000.00', b['answer'])
        self.assertNotIn('какую сумму нужно сохранить', b['answer'])


class CalculationTests(unittest.TestCase):
    def q(self, value, unit='rub', period=''):
        return Quantity(Decimal(value), unit, 'user message', period)

    def test_operations_units_periods_rounding_and_provenance(self):
        self.assertEqual(calculate('available', {'budget': self.q('10000000'), 'reserve': self.q('2000000')})['value'], '8000000.00')
        self.assertEqual(calculate('cost', {'unit_price': self.q('2000000'), 'quantity': self.q('3', 'count')})['value'], '6000000.00')
        result = calculate('net', {'revenue': self.q('300', period='month'), 'costs': self.q('100', period='month')})
        self.assertEqual(result['value'], '200.00')
        self.assertEqual(result['period'], 'month')
        result = calculate('payback', {'investment': self.q('1000'), 'net': self.q('300', period='month')})
        self.assertEqual(result['value'], '3.33')
        self.assertTrue(result['inputs']['net']['origin'])
        self.assertTrue(result['assumption'])
        self.assertEqual(calculate('difference', {'first': self.q('100'), 'second': self.q('120')})['value'], '-20.00')

    def test_invalid_operations_and_missing_incompatible_zero_inputs(self):
        for operation, inputs in [('eval', {}), ('available', {}), ('available', {'budget': self.q('1'), 'reserve': self.q('2')}),
            ('net', {'revenue': self.q('100', period='month'), 'costs': self.q('10', period='year')}),
            ('difference', {'first': self.q('100'), 'second': self.q('100', 'usd')}),
            ('payback', {'investment': self.q('100'), 'net': self.q('0', period='month')}),
            ('cost', {'unit_price': self.q('100'), 'quantity': self.q('1.5', 'count')})]:
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                calculate(operation, inputs)
        for value in ['-1', 'NaN', 'Infinity']:
            with self.assertRaises(ValueError):
                self.q(value)

    def test_user_and_calculated_numbers_allowed_but_stale_or_wrong_units_rejected(self):
        context = saved()['context']
        context['_task'] = task_memory({}, [], 'Расширяем автопарк. Бюджет 10 млн рублей, резерв 2 млн рублей. Выручка 300 тысяч рублей в месяц.', 0)
        context['_calculations'] = ledger_for(context['_task']['facts'])
        verify_answer(AnswerResponse(answer='По вашим данным бюджет 10 млн рублей; после резерва доступно 8 млн рублей.', warnings=['Условия сообщены вами.']), context)
        for text in ['Ваш бюджет 10 млн долларов.', 'По вашим данным выручка 300 тысяч рублей в год.',
                     'По вашим данным выручка 10 млн рублей.', 'Ваш бюджет 9 млн рублей.', 'Банк предлагает кредит под 10%.']:
            with self.subTest(text=text), self.assertRaises(EvidenceError):
                verify_answer(AnswerResponse(answer=text, warnings=['Условия сообщены вами.']), context)

    def test_forecast_cannot_become_a_decision_or_current_rate(self):
        context = saved()['context']
        context['evidence']['103']['text'] = 'Мы ожидаем, что ключевая ставка сохранится на уровне 14% в 2027 году.'
        for text in ['ЦБ сохранил ключевую ставку на уровне 14%.', 'Сегодня ключевая ставка 14%.']:
            with self.assertRaises(EvidenceError):
                verify_answer(AnswerResponse(answer=text, source_ids=['103']), context)
        context['evidence']['103']['text'] = 'ЦБ сохранил ключевую ставку на уровне 14%.'
        verify_answer(AnswerResponse(answer='ЦБ сохранил ключевую ставку на уровне 14%.', source_ids=['103']), context)
        with self.assertRaises(EvidenceError):
            verify_answer(AnswerResponse(answer='ЦБ снизил ключевую ставку на уровне 14%.', source_ids=['103']), context)
        with self.assertRaises(EvidenceError):
            verify_answer(AnswerResponse(answer='Проверьте условия.', source_ids=['103'], warnings=['Банк предлагает кредит под 2%.']), context)


class RankingTests(unittest.IsolatedAsyncioTestCase):
    def test_matrix_development_and_held_out(self):
        fixture = json.loads((Path(__file__).parents[1] / 'data/relevance_cases.json').read_text())
        profiles = {p['company_id']: p for p in fixture['profiles']}
        news = {n['id']: n for n in fixture['news']}
        for row in fixture['cases']:
            with self.subTest(**row):
                ranked = rank_news(profiles[row['profile']], [news[row['news']]])
                self.assertEqual(bool(ranked), row['relevant'])
                if ranked:
                    self.assertTrue(ranked[0]['relevance_reasons'])
                    ensure_human(*(r['reason'] for r in ranked[0]['relevance_reasons']))

    def test_virtual_and_real_construction_and_secondary_rate_mention(self):
        profile = {'industry': 'Строительство', 'features': {'industry_code': '41.20'}}
        self.assertFalse(rank_news(profile, [{'id': 'a', 'title': 'Видеоигра о строительстве виртуальных зданий', 'body': ''}]))
        self.assertTrue(rank_news(profile, [{'id': 'b', 'title': 'Для игровой компании начали строительство офиса', 'body': ''}]))
        rental = {'industry': 'Аренда автомобилей', 'features': {'industry_code': '77.11', 'bank_products': {'leasing': True}}}
        self.assertFalse(rank_news(rental, [{'id': 'c', 'title': 'РЖД меняет тарифы на грузовые перевозки', 'body': 'Изменяются тарифы. Впрочем, ключевая ставка тоже влияет на бизнес.'}]))

    def test_no_id_dependence_and_dedup_preserves_distinct_decisions(self):
        p = {'company_id': 'arbitrary', 'features': {'industry_code': '77.11'}}
        a = {'id': 'a', 'title': 'Автомобили подорожали', 'body': 'Автомобили подорожали.', 'published_at': '2026-10-20'}
        b = {**a, 'id': 'b', 'body': a['body'] * 3}
        c = {**a, 'id': 'c', 'published_at': '2026-10-21'}
        self.assertEqual([r['id'] for r in rank_news(p, [a, b, c])], ['c', 'b'])
        self.assertEqual(rank_news(p, [a])[0]['relevance_score'], rank_news({**p, 'company_id': 'new'}, [a])[0]['relevance_score'])

    def test_real_snapshot_counterexamples_and_decision_dedup(self):
        rental = {'industry': 'Аренда автомобилей', 'features': {'industry_code': '77.11', 'region': 'Республика Татарстан', 'bank_products': {'leasing': True}}}
        for title, body in [
            ('Грантовая поддержка IT позволила индустрии заработать', 'Отечественные решения внедряются в авиа- и автомобилестроении.'),
            ('Минпромторг предложил ввести утильсбор на буровое оборудование', 'Буровое оборудование облагается утилизационным сбором.'),
            ('В Рязани отменяют продажу топлива по номерам машин', 'Отменяют систему продажи бензина по номерам автомобилей.'),
            ('Кредитный портфель банка вырос за счёт корпоративных кредитов', 'Чистая прибыль выросла.'),
            ('Банки повысили ставки по кредитам наличными', 'Изменились ставки потребительского кредитования населения.'),
            ('РЖД меняет грузовые тарифы', 'Грузовые перевозки подорожали.'),
            ('Изменения логистики складов', 'Склады меняют стоимость хранения.'),
            ('Продавцам маркетплейсов предоставят кредитные каникулы', 'Снижение нагрузки на продавцов.')]:
            self.assertFalse(rank_news(rental, [{'id': 'a', 'title': title, 'body': body}]), title)
        rows = [{'id': 'a', 'title': 'ЦБ сохранил ключевую ставку на уровне 14%', 'body': '', 'published_at': '2026-10-23'},
                {'id': 'b', 'title': 'Банк России принял решение сохранить ключевую ставку на уровне 14,00% годовых', 'body': 'Подробности решения.', 'published_at': '2026-10-23'},
                {'id': 'c', 'title': 'ЦБ снизил ключевую ставку на уровне 13%', 'body': '', 'published_at': '2026-10-23'}]
        self.assertEqual(len(rank_news(rental, rows)), 2)
        for name in ('Прокат легковых машин', 'Аренда и лизинг легковых автомобилей'):
            changed = {**rental, 'company_id': 'unknown-new', 'industry': name}
            self.assertTrue(rank_news(changed, [{'id': 'new', 'title': 'Подорожали автомобили для автопарков', 'body': ''}]))

    async def test_pagination_and_legacy_lister(self):
        calls = []
        async def profile(identifier): return {'company_id': identifier, 'industry': 'Аренда автомобилей', 'features': {'industry_code': '77.11'}}
        async def listing(*, limit, offset=0):
            calls.append(offset)
            if offset == 0: return [{'id': str(i), 'title': 'Метеорит', 'body': ''} for i in range(200)]
            return [{'id': 'late', 'title': 'Стоимость автомобилей выросла', 'body': ''}]
        tools = ServiceTools(company_id='c', llm=None, profile_loader=profile, news_loader=None, news_lister=listing, bank_searcher=None, corpus_version='none')
        result = await recommend_news('c', tools)
        self.assertEqual(calls, [0, 200])
        self.assertEqual(result['items'][0]['id'], 'late')
        async def legacy(*, limit): return await listing(limit=limit, offset=200)
        tools.news_lister = legacy
        self.assertTrue((await recommend_news('c', tools))['items'])


class SearchTests(unittest.IsolatedAsyncioTestCase):
    def material(self):
        return {'chunk_id': 'checked', 'document_id': 'report', 'document_title': 'Обзор лизинга оборудования', 'page_number': 2,
                'published_at': '2026-09-11', 'text': 'Лизинг оборудования позволяет распределить платежи во времени. Итоговые условия зависят от договора.',
                'source_url': None, 'reviewed': True, 'origin': 'bank'}

    async def test_extra_material_requires_persistence_and_textual_attribution(self):
        example = saved()
        llm = Replies(ChatDraft(answer='Сравните график платежей и полную стоимость лизинга оборудования.', source_ids=['bank:checked']))
        calls = []
        async def search(query, **kw): calls.append(query); return [self.material()]
        tools = SimpleNamespace(company_id=example['company_id'], llm=llm, search_chat_evidence=search, _can_save_chat=True)
        before = copy.deepcopy(example)
        result = await answer_question(example, 'Как лизинг оборудования распределяет платежи во времени?', tools)
        self.assertLessEqual(len(calls), 2)
        self.assertNotIn('bank:checked', result['source_ids'])
        self.assertIn('Альфа-Банка', result['answer'])
        self.assertIn('2026-09-11', result['answer'])
        self.assertIn('страница 2', result['answer'])
        self.assertIn('bank:checked', tools._chat_update['evidence'])
        self.assertEqual(example, before)

    async def test_no_extra_sources_with_old_repository_and_search_failure_is_optional(self):
        from app.agent.chat_search import expand_evidence
        from app.agent.chat import chat_context
        example = saved()
        async def search(*args, **kw): return [self.material()]
        tools = SimpleNamespace(company_id=example['company_id'], llm=object(), search_chat_evidence=search)
        context = chat_context(example)
        self.assertEqual(await expand_evidence(example, context, 'Как лизинг оборудования распределяет платежи?', tools), {})
        async def failed(*args, **kw): raise RuntimeError('SELECT sensitive FROM companies')
        tools.search_chat_evidence = failed
        self.assertEqual(await expand_evidence(example, context, 'Как лизинг оборудования распределяет платежи?', tools), {})

    async def test_simple_question_never_initializes_search(self):
        example = saved()
        async def search(*args, **kwargs): self.fail('unneeded search')
        tools = SimpleNamespace(company_id=example['company_id'], llm=Replies(ChatDraft(answer='Уточните действие и проверьте внутренние правила.', source_ids=['103'])), search_chat_evidence=search)
        await answer_question(example, 'Что делать?', tools)


class BudgetTests(unittest.IsolatedAsyncioTestCase):
    async def test_total_output_budget_and_lower_deadline(self):
        from app.agent.service import complete_json
        tools = SimpleNamespace(llm=Replies(ChatDraft(answer='Проверьте условия.', warnings=['Нужны условия.'])))
        state = AgentState('c', 'n', output_limit=4000, output_reserved=3500)
        await complete_json(tools, state, AgentLimits(), 'ask', {}, ChatDraft)
        self.assertEqual(tools.llm.calls[0][1]['max_tokens'], 500)
        with self.assertRaises(EvidenceError):
            await complete_json(tools, state, AgentLimits(), 'ask', {}, ChatDraft)
        class Slow:
            async def complete(self, *args, **kwargs): await asyncio.sleep(1)
        result = await answer_question(saved(), 'Что делать?', SimpleNamespace(company_id='U_10002', llm=Slow()), limits=AgentLimits(deadline_seconds=.01))
        self.assertTrue(any('Истекло' in w for w in result['warnings']))
