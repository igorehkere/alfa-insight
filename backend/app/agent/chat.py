"""Grounded business assistance with bounded memory, search and one repair."""
import asyncio
import copy
import logging
import re
from time import perf_counter

from pydantic import Field, ValidationError

from app.agent.calculations import ledger_for, calculation_presentation
from app.agent.errors import AgentUnavailable, EvidenceError, InvalidInput
from app.agent.language import compact_warnings, ensure_human
from app.agent.memory import task_memory, memory_presentation
from app.agent.schema import AnswerResponse, AskRequest, NonEmptyText
from app.agent.state import AgentLimits, AgentState
from app.agent.verifier import normalized, verify_answer

CHAT_OUTPUT_TOKENS = 1600
logger = logging.getLogger(__name__)


class ChatDraft(AnswerResponse):
    answer: NonEmptyText = Field(max_length=6000)
    user_context: NonEmptyText | None = Field(default=None, max_length=180)


def recent_dialogue(history=None, turn_count=None):
    history = history or []
    count = len(history) if turn_count is None else turn_count
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise InvalidInput('turn_count должен быть неотрицательным числом')
    result = []
    for row in history[-12:]:
        question = row.get('question', '')
        answer = row.get('response', {}).get('answer', '')
        if isinstance(question, str) and isinstance(answer, str):
            result.append({'question': question[:2000], 'answer': answer[:700]})
    return result, count


def verified_answer(draft, context, question, *, previous_questions=()):
    answer = AnswerResponse(**draft.model_dump(exclude={'user_context'}))
    verify_answer(answer, context)
    user_topic = ' '.join((question, *previous_questions)).lower()
    news_topic = ' '.join((str(context.get('news', {}).get('title') or ''),
                           context.get('evidence', {}).get(context.get('news_id'), {}).get('text', ''))).lower()
    financing = r'кредит|лизинг|финансирован|оборудован|станк|автопарк|машин'
    if re.search(r'кредит|лизинг|финансирован', answer.answer.lower()) and not re.search(financing, user_topic + ' ' + news_topic):
        raise EvidenceError('Совет о финансировании не связан с вопросом и выбранной новостью')
    if draft.user_context:
        if not any(normalized(draft.user_context) in normalized(q) for q in (question, *previous_questions)):
            raise EvidenceError('Пользовательское условие должно быть взято из его сообщения без новых данных')
        ensure_human(draft.user_context)
        answer.answer = f'По вашим словам: «{draft.user_context}»\n\n{answer.answer}'
        answer.warnings.append("Условия сообщены вами; они не подтверждают индивидуальные условия банка.")
    answer.warnings = compact_warnings(answer.warnings)
    return answer


def general_answer(question, *, unavailable=False):
    return AnswerResponse(answer='Сравните доступные действия по затратам, сроку и обратимости. Сначала проверьте условие, от которого зависит выбор.',
                          source_ids=[], warnings=['Это качественная помощь по вашей задаче; индивидуальные условия банка не подтверждены.'])


def evidence_excerpt(text, anchors=(), budget=1800):
    """A contiguous window of complete sentences, retaining adjacent conditions."""
    if len(text) <= budget:
        return text
    boundaries = [0, *[m.end() for m in re.finditer(r'(?<=[.!?])\s+(?=[А-ЯЁA-Z«])|\n\s*\n', text)], len(text)]
    target = next((text.find(a) for a in anchors if a and a in text), 0)
    index = max(i for i, start in enumerate(boundaries[:-1]) if start <= target)
    start, end = boundaries[index:index + 2]
    if end - start > budget:
        return ''
    if index and end - boundaries[index - 1] <= budget:
        index -= 1
        start = boundaries[index]
    while index + 2 < len(boundaries) and boundaries[index + 2] - start <= budget:
        index += 1
        end = boundaries[index + 1]
    return text[start:end].strip()


def contextual_answer(context, question, dialogue, reason):
    memory = context.get('_task', {})
    topic = memory.get('topic')
    if not topic:
        from app.agent.memory import topic_for
        topic = topic_for(str(context.get('news', {}).get('title', '')))
    conditions = memory_presentation(memory)
    prefix = ('По вашим данным: ' + '; '.join(conditions) + '.\n\n') if conditions else ''
    facts = memory.get('facts', {})
    if topic in {'fleet', 'equipment', 'construction', 'stock'}:
        target = {'fleet': 'автопарк', 'equipment': 'оборудование', 'construction': 'проект', 'stock': 'запас товаров'}[topic]
        occupied = 'utilization' in facts or 'utilization_note' in facts
        demand = 'demand_note' in facts
        first = ('Полная занятость сама по себе не доказывает прибыльный спрос сверх текущих возможностей. ' if occupied and not demand else
                 'Проверьте прибыльность дополнительных заказов после переменных расходов. ' if demand else
                 'Сначала сопоставьте подтверждённые заказы с доступными мощностями. ')
        advice = (first + f'Можно расширять {target} сразу, поэтапно либо временно привлекать внешние мощности. '
                  'Выбор зависит от устойчивости дополнительного спроса, срока запуска и суммы, которая останется для текущей работы. ')
        if 'budget' in facts:
            advice += 'Указанный вами бюджет ограничивает разовую покупку; сравните затраты с доступной суммой после резерва. '
        if demand:
            advice += 'При устойчивых прибыльных заказах сравните покупку и временное привлечение по полной стоимости за одинаковый срок. '
        else:
            advice += 'Проверьте журнал отказов клиентам и прибыль от этих заказов; при неопределённом спросе поэтапное расширение уменьшает необратимые затраты. '
        missing = []
        if 'reserve' not in facts:
            missing.append('какую сумму нужно сохранить как резерв')
        if 'unit_price' not in facts:
            missing.append('какова цена единицы с запуском')
        if not occupied and not demand:
            missing.append('есть ли заказы сверх текущих возможностей')
        if missing:
            advice += 'Для сравнения уточните: ' + '; '.join(missing[:3]) + '?'
    elif topic == 'employee':
        advice = ('Для действий сотрудника важны его обязанности и доступ к информации. Сравните обычное рабочее действие '
                  'и ситуацию с доступом к непубличным сведениям: во втором случае сначала нужна проверка внутренних правил комплаенса. '
                  'Могу составить перечень конкретных вопросов для ответственного за комплаенс. '
                  + ('Какое действие вызывает сомнение?' if 'role' in facts else 'Какова ваша роль и какое действие вызывает сомнение?'))
    elif topic == 'hiring':
        advice = ('Сравните найм постоянного сотрудника, временного исполнителя и перераспределение задач. '
                  'Постоянный найм оправдан устойчивой нагрузкой, временный — ограниченным сроком работы. '
                  'Следующий шаг — собрать перечень задач, оценить их объём и стоимость каждого варианта за одинаковый период. Какую задачу и срок закрывает найм?')
    elif topic == 'deposit':
        advice = ('Сравните размещение всей свободной суммы на срок и размещение частями с разными датами возврата. '
                  'Критерии — доступ к деньгам, срок и условия досрочного возврата. Сначала составьте календарь обязательных платежей; '
                  'средства на ближайшие платежи должны оставаться доступными. Каков срок размещения и потребность в досрочном доступе?')
    elif topic == 'trade':
        advice = ('Сравните доступные пути расчётов по валюте, сроку зачисления, комиссии и риску возврата. '
                  'Сначала проверьте допустимость платежа по конкретному договору и требования к документам. '
                  'Какие валюта и направление платежа указаны в договоре?')
    else:
        advice = ('Определите, какие затраты, сроки или договоры меняет событие. Сравните действие сейчас и ожидание подтверждающих условий: '
                  'критерии — цена задержки и обратимость затрат. Начните с проверки затрагиваемых договоров и даты публикации. '
                  'Какое решение вы принимаете по этой новости?')
    answer = general_answer(question)
    if context.get('_calculations'):
        labels = {'available': 'После указанного вами резерва доступно', 'cost': 'Затраты на указанное вами количество',
                  'net': 'Результат после сообщённых вами расходов', 'payback': 'Простая окупаемость при неизменном результате'}
        units = {'rub': 'рублей', 'usd': 'долларов', 'eur': 'евро', 'month': 'месяцев'}
        rows = []
        for item in context['_calculations']:
            period = {'month': ' в месяц', 'year': ' в год', 'day': ' в день'}.get(item['period'], '')
            rows.append(f"{labels[item['operation']]}: {item['value']} {units[item['unit']]}{period}.")
        prefix += 'По вашим данным, локальный расчёт: ' + ' '.join(rows) + '\n\n'
    answer.answer = prefix + advice
    explanations = {
        'no_evidence': 'Сохранённых выдержек недостаточно для точного вывода; предложен порядок проверки.',
        'rejected': 'Не удалось подтвердить подробный ответ; показана общая помощь без спорных утверждений.',
        'unavailable': 'Сервис объяснений временно недоступен; показан локально подготовленный ответ.',
        'timeout': 'Истекло время подготовки объяснения; показан локально подготовленный ответ.',
    }
    answer.warnings.append(explanations[reason])
    ensure_human(answer.answer, *answer.warnings)
    return answer


def chat_context(insight, question=''):
    saved = insight['context']
    ids = {source['id'] for source in insight['result']['sources']}
    rows = [(sid, item) for sid, item in saved.get('evidence', {}).items() if sid in ids]
    rows.sort(key=lambda row: (row[0] != saved['news_id'], row[1].get('source', {}).get('type') != 'bank', row[0]))
    from app.agent.recommendations import industry_terms
    terms = industry_terms(question)
    evidence = {}
    for sid, item in rows[:6]:
        statements = [*insight['result'].get('what_happened', []), insight['result'].get('bank_position', {})]
        anchors = [row.get('text') for row in statements if sid in row.get('source_ids', [])]
        if terms:
            sentences = re.split(r'(?<=[.!?])\s+(?=[А-ЯЁA-Z«])|\n\s*\n', item.get('text', ''))
            relevant = sorted(sentences, key=lambda text: -len(terms & industry_terms(text)))
            anchors = [s for s in relevant[:2] if terms & industry_terms(s)] + anchors
        excerpt = evidence_excerpt(item.get('text', ''), anchors)
        if excerpt:
            evidence[sid] = copy.deepcopy(item)
            evidence[sid]['text'] = excerpt
            evidence[sid]['chat_excerpt_limited'] = excerpt != item.get('text', '')
    profile = saved.get('profile', {})
    return {'company_id': saved['company_id'], 'news_id': saved['news_id'], 'as_of': saved.get('as_of'),
            'profile': {key: copy.deepcopy(profile[key]) for key in ('company_id', 'industry', 'industry_code', 'region', 'features') if key in profile},
            'news': {key: saved.get('news', {}).get(key) for key in ('id', 'title', 'published_at', 'provenance', 'body_is_summary')},
            'evidence': evidence}


async def answer_question(insight, question, tools, *, history=None, turn_count=None, limits=None):
    from app.agent.service import complete_json
    from app.agent.chat_search import expand_evidence, attribute_additions
    try:
        question = AskRequest(question=question).question
    except ValidationError:
        raise InvalidInput('Вопрос должен содержать от 1 до 2000 символов') from None
    saved = insight.get('context')
    if not saved or saved.get('company_id') != insight.get('company_id') or saved.get('news_id') != insight.get('news_id'):
        raise InvalidInput('Для чата нужен сохранённый контекст этого анализа')
    if getattr(tools, 'company_id', None) != saved['company_id']:
        raise InvalidInput('Чат относится к другой компании')
    dialogue, count = recent_dialogue(history, turn_count)
    memory = task_memory(saved, dialogue, question, count)
    start = count - len(dialogue)
    dialogue = [row for i, row in enumerate(dialogue) if start + i >= memory.get('since', 0)]
    context = chat_context(insight, question)
    context['_task'] = memory
    context['_calculations'] = ledger_for(memory['facts'])
    state = AgentState(context['company_id'], context['news_id'], output_limit=4000)
    base_limits = limits or AgentLimits(deadline_seconds=90)
    base_limits = base_limits.model_copy(update={'deadline_seconds': min(90, base_limits.deadline_seconds)})
    complex_question = len(question) > 350 or bool(re.search(r'сравн|вариант|рассчит|окупаем|бюджет|автопарк', question.lower()))
    limits = base_limits.model_copy(update={'analysis_tokens': min(base_limits.analysis_tokens, 2400 if complex_question else CHAT_OUTPUT_TOKENS)})
    started = perf_counter()

    def fallback(reason):
        logger.info('chat reason=%s generations=%s elapsed=%.3f usage=%s', reason, state.llm_calls, perf_counter() - started, state.usage)
        return contextual_answer(context, question, dialogue, reason)

    additions = {}

    async def respond():
        nonlocal additions
        additions = await expand_evidence(insight, context, question, tools)
        if tools.llm is None:
            return fallback('unavailable')
        if not context['evidence']:
            return fallback('no_evidence')
        error = None
        for attempt in range(2):
            try:
                current_limits = limits if attempt == 0 else limits.model_copy(update={'analysis_tokens': min(limits.analysis_tokens, 1600)})
                # Original user messages stay in local provenance. The model sees compact active values.
                task = {key: memory[key] for key in ('topic', 'goal') if key in memory}
                task['conditions'] = memory_presentation(memory)
                draft = await complete_json(tools, state, current_limits, 'ask', {
                    'context': {k: v for k, v in context.items() if not k.startswith('_')},
                    'question': question, 'history': [{'question': row['question'][:600], 'answer': row['answer'][:400]} for row in dialogue[-3:]],
                    'task_memory': task, 'calculations': calculation_presentation(context['_calculations']),
                    'analysis_summary': {'ai_interpretation': str(insight['result'].get('ai_interpretation', ''))[:500],
                                         'suggested_actions': insight['result'].get('suggested_actions', [])[:3]},
                    'instruction': 'Отвечай по задаче и активным условиям пользователя. История и прежние выводы не являются доказательствами. '
                                   'Дай варианты, критерий выбора и доступное действие, если это полезно. Не требуй уже известных сведений. '
                                   'Числа пользователя атрибутируй ему; расчёты бери только из реестра. При достаточных данных отвечай прямо. '
                                   'Служебные пути разрешены только в метаданных. Пиши понятными словами.',
                    'verification_error': error,
                }, ChatDraft)
                answer = verified_answer(draft, context, question, previous_questions=[row['question'] for row in dialogue])
                attribute_additions(answer, additions, insight)
                return answer
            except AgentUnavailable:
                return fallback('unavailable')
            except EvidenceError as exc:
                # Controlled verifier messages only; never raw provider output.
                error = str(exc) + '. Исправь смысл и формулировку без выдуманных фактов.'
                logger.info('chat reason=rejected attempt=%s', attempt + 1)
        return fallback('rejected')

    try:
        answer = await asyncio.wait_for(respond(), limits.deadline_seconds)
    except TimeoutError:
        answer = fallback('timeout')
    if context['news'].get('body_is_summary'):
        answer.warnings.append('Доступно краткое описание новости; полный текст не проверялся.')
    if any(term in question.lower() for term in ('сейчас', 'сегодня', 'изменилось', 'актуаль')):
        available_date = context.get('as_of') or context['news'].get('published_at')
        answer.warnings.append(f"Доступны сохранённые сведения на дату {available_date or 'неизвестна'}; последующие изменения не проверялись.")
    if getattr(tools.llm, 'offline', False):
        answer.warnings.append('Офлайн-демонстрация: ответ составлен без языковой модели.')
    answer.warnings = compact_warnings(answer.warnings)
    ensure_human(answer.answer, *answer.warnings)
    if getattr(tools, '_can_save_chat', False):
        tools._chat_update = {'memory': memory, 'calculations': context['_calculations'],
                              'evidence': {sid: item for sid, item in additions.items() if sid in getattr(answer, '_used_additions', [])},
                              'usage': state.usage, 'turn_count': count}
    logger.info('chat finished generations=%s reserved=%s elapsed=%.3f usage=%s', state.llm_calls, state.output_reserved, perf_counter() - started, state.usage)
    return answer.model_dump(mode='json')
