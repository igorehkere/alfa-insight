"""Offline regression checks for summary matching and qualitative fallback."""
import copy
import json
import unittest
from unittest.mock import patch

from app.agent.errors import EvidenceError
from app.agent.recommendations import recommend_news, rank_news
from app.agent.relevance import RelevanceEngine, company_facts, news_brief, no_relevance
from app.agent.schema import InsightResult, LLMReply
from app.agent.service import run_analysis
from app.agent.tools import news_from_record
from app.agent.verifier import verify_result


PROFILE = {'company_id': 'builder', 'industry': 'Строительство зданий', 'features': {}}
NEWS = news_from_record({
    'id': 'cement', 'title': 'Перебои поставок цемента',
    'content': 'Поставщики сообщили о перебоях поставок цемента для строительных работ. Цены выросли на 10%.',
    'description': 'Перебои поставок цемента могут затронуть строительные работы.',
    'published_at': '2026-10-20',
})
DESCRIPTION = {'activity': 'Строительство зданий', 'dependencies': ['Строительные материалы'],
               'sensitivities': ['Стоимость и доступность материалов'],
               'assumptions': ['Состав закупок неизвестен'], 'search_terms': ['цемент']}
ASSESSMENT = {'level': 'high', 'fields': ['industry'], 'quote': 'Перебои поставок цемента',
              'event': 'Перебои поставок', 'affected': 'Строительные компании',
              'mechanism': 'Перебои могут затруднить закупку материалов для строительных работ',
              'conditions': []}


class SummaryLLM:
    cache_identity = 'summary-test'

    def __init__(self):
        self.requests = []
        self.plan_relevance = copy.deepcopy(ASSESSMENT)
        self.bad_quote = False

    async def complete(self, messages, **kwargs):
        request = json.loads(messages[-1]['content'])
        self.requests.append(request)
        stage, data = request['stage'], request['data']
        if stage == 'company_profile':
            result = DESCRIPTION
        elif stage == 'news_relevance':
            result = {'items': [dict(ASSESSMENT, i=item['i'],
                                    quote='Выдуманная выдержка' if self.bad_quote else ASSESSMENT['quote'])
                                for item in reversed(data['news'])]}
        elif stage in {'plan', 'replan'}:
            result = {'search_queries': ['цемент' if stage == 'plan' else 'поставки цемента'],
                      'relevance': self.plan_relevance}
        elif stage in {'analyze', 'repair'}:
            unrelated = (data.get('event_relevance') or {}).get('level') == 'none'
            source = data['evidence'][data['news_id']]['source']
            result = {
                'what_happened': [{'text': NEWS['body'], 'source_ids': [NEWS['id']]}],
                'why_relevant': [] if unrelated else [
                    {'text': 'Строительство зависит от доступности материалов.', 'company_fields': ['industry']}],
                'risks': [] if unrelated else [{
                    'text': 'Если компания закупает цемент, перебои могут задержать строительные работы.',
                    'source_ids': [NEWS['id']], 'certainty': 'conditional'}],
                'opportunities': [] if unrelated else [{
                    'text': 'При наличии альтернативных поставщиков компания может диверсифицировать закупки.',
                    'source_ids': [NEWS['id']], 'certainty': 'conditional'}],
                'bank_position': {'status': 'not_found', 'text': None, 'source_ids': []},
                'ai_interpretation': ('Связь с деятельностью компании не установлена.' if unrelated else
                    'Условная оценка AI: перебои могут осложнить закупки материалов. Состав закупок компании неизвестен.'),
                'suggested_actions': ['Проверить запасы материалов и условия действующих поставок.'],
                'sources': [source], 'warnings': [],
            }
        else:
            raise AssertionError(stage)
        return LLMReply(text=json.dumps(result, ensure_ascii=False), model=self.cache_identity,
                        elapsed_seconds=0, finish_reason='stop')


class SummaryTools:
    corpus_version = 'empty-test-corpus'

    def __init__(self, llm):
        self.llm = llm
        self.relevance_engine = RelevanceEngine(llm)
        self.searches = []

    async def get_company_profile(self, company_id):
        return copy.deepcopy(PROFILE)

    async def get_news(self, news_id):
        return copy.deepcopy(NEWS)

    async def list_news(self, limit=200):
        return [copy.deepcopy(NEWS)]

    async def search_bank_analytics(self, query, limit, *, as_of):
        self.searches.append(query)
        return []


class SummaryAnalysisTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        block = patch('httpx.AsyncClient.send', side_effect=AssertionError('No paid requests'))
        block.start()
        self.addCleanup(block.stop)
        self.llm = SummaryLLM()
        self.tools = SummaryTools(self.llm)

    async def asyncTearDown(self):
        await self.tools.relevance_engine.aclose()

    async def test_feed_and_analysis_use_same_summaries_and_preserve_cached_relevance(self):
        engine = self.tools.relevance_engine
        context = await engine.company_context(PROFILE)
        assessments = await engine.assess(context, [NEWS])
        batch = self.llm.requests[-1]['data']
        self.assertEqual(batch['business_context'], DESCRIPTION)
        self.assertEqual(batch['news'][0]['text'], NEWS['summary'])
        self.assertNotIn('Цены выросли', batch['news'][0]['text'])
        self.assertEqual(rank_news(PROFILE, [NEWS], assessments=assessments)[0]['relevance_level'], 'high')
        # A later search planner must not silently reverse a checked feed decision.
        self.llm.plan_relevance = no_relevance()
        run = await run_analysis('builder', 'cement', self.tools)
        self.assertEqual(run.context['event_relevance']['level'], 'high')
        self.assertEqual(run.context['analysis_policy']['mode'], 'qualitative')
        self.assertEqual(run.analysis['result']['bank_position']['status'], 'not_found')
        self.assertTrue(run.analysis['result']['risks'])
        self.assertTrue(run.analysis['result']['opportunities'])
        for request in self.llm.requests:
            if request['stage'] in {'plan', 'replan', 'analyze', 'repair'}:
                self.assertEqual(request['data']['news'], news_brief(NEWS))
                self.assertEqual(request['data']['business_context'], context)
        self.assertTrue(any('Качественная оценка' in w for w in run.analysis['result']['warnings']))

    async def test_analysis_decision_is_reused_in_feed(self):
        run = await run_analysis('builder', 'cement', self.tools)
        self.assertEqual(run.context['event_relevance']['level'], 'high')
        count = len(self.llm.requests)
        feed = await recommend_news('builder', self.tools)
        self.assertEqual(feed['ranking_status'], 'ready')
        self.assertEqual(feed['items'][0]['relevance_method'], 'llm')
        self.assertEqual(feed['items'][0]['relevance_level'], 'high')
        self.assertEqual(len(self.llm.requests), count)

    async def test_cached_no_connection_prevents_fabricated_consequences(self):
        engine = self.tools.relevance_engine
        context = await engine.company_context(PROFILE)
        engine.remember_assessment(context, NEWS, None)
        run = await run_analysis('builder', 'cement', self.tools)
        self.assertEqual(run.context['event_relevance']['level'], 'none')
        self.assertEqual(self.tools.searches, [])
        self.assertEqual(run.analysis['result']['risks'], [])
        self.assertEqual(run.analysis['result']['opportunities'], [])

    async def test_bank_search_hit_can_be_declined_when_not_useful(self):
        async def weak_search(*args, **kwargs):
            return [{'chunk_id': 'general', 'document_id': 'overview', 'document_title': 'Общий обзор',
                     'page_number': 1, 'published_at': '2026-10-19', 'reviewed': True, 'origin': 'bank',
                     'text': 'Поставки цемента для строительных работ упомянуты в общем обзоре. Влияние на компанию не оценивалось.'}]
        self.tools.search_bank_analytics = weak_search
        run = await run_analysis('builder', 'cement', self.tools)
        self.assertIn('bank:general', run.context['evidence'])
        self.assertEqual(run.analysis['result']['bank_position']['status'], 'not_found')
        self.assertTrue(run.analysis['result']['risks'])
        self.assertEqual([source['type'] for source in run.analysis['result']['sources']], ['news'])

    async def test_full_article_is_saved_but_only_an_excerpt_is_sent_to_analysis(self):
        long_news = {**NEWS, 'body': NEWS['body'] + ' Дополнительное описание события.' * 140}
        async def loader(news_id):
            return long_news
        self.tools.get_news = loader
        run = await run_analysis('builder', 'cement', self.tools)
        request = next(row for row in self.llm.requests if row['stage'] == 'analyze')
        self.assertEqual(run.context['evidence']['cement']['text'], long_news['body'])
        self.assertLessEqual(len(request['data']['evidence']['cement']['text']), 1800)
        self.assertEqual(request['data']['news']['body'], NEWS['summary'])

    async def test_invalid_model_evidence_does_not_cache_irrelevance(self):
        engine = self.tools.relevance_engine
        context = await engine.company_context(PROFILE)
        self.llm.bad_quote = True
        self.assertEqual(await engine.assess(context, [NEWS]), {})
        self.assertEqual(engine.cached_assessments(context, [NEWS]), {})
        self.llm.bad_quote = False
        self.assertEqual((await engine.assess(context, [NEWS]))['cement']['level'], 'high')

    async def test_summary_change_invalidates_event_cache(self):
        engine = self.tools.relevance_engine
        context = await engine.company_context(PROFILE)
        await engine.assess(context, [NEWS])
        changed = {**NEWS, 'summary': 'Прежнее сообщение о перебоях поставок опровергнуто.'}
        self.assertEqual(engine.cached_assessments(context, [changed]), {})

    async def test_qualitative_effect_cannot_reuse_a_number_as_company_estimate(self):
        run = await run_analysis('builder', 'cement', self.tools)
        result = InsightResult.model_validate(run.analysis['result'])
        for target in [result.risks[0], result.opportunities[0], result.why_relevant[0]]:
            altered = result.model_copy(deep=True)
            if target is result.risks[0]:
                altered.risks[0].text = 'Расходы компании могут вырасти на 10%.'
            elif target is result.opportunities[0]:
                altered.opportunities[0].text = 'Компания может сэкономить 10%.'
            else:
                altered.why_relevant[0].text = 'Для компании затраты вырастут на 10%.'
            with self.assertRaises(EvidenceError):
                verify_result(altered, run.context)
        # Original news figures remain available as sourced facts.
        verify_result(result, run.context)
        self.assertIn('10%', result.what_happened[0].text)

    async def test_relevant_qualitative_analysis_requires_a_grounded_consequence(self):
        run = await run_analysis('builder', 'cement', self.tools)
        result = InsightResult.model_validate(run.analysis['result'])
        result.risks = []
        result.opportunities = []
        with self.assertRaises(EvidenceError):
            verify_result(result, run.context)

    def test_brief_is_bounded_without_discarding_existing_summary(self):
        self.assertEqual(news_brief(NEWS)['body'], NEWS['summary'])
        self.assertLessEqual(len(news_brief({**NEWS, 'summary': '', 'body': 'А' * 8000})['body']), 1200)
        self.assertEqual(company_facts(PROFILE), {'industry': PROFILE['industry']})


if __name__ == '__main__':
    unittest.main()
