"""Bounded task memory extracted only from user messages, never assistant guesses."""
import copy
import re
from decimal import Decimal

TOPICS = {
    'fleet': r'автопарк|машин|автомобил|таксопарк',
    'equipment': r'оборудован|станк|производственн\w* лини',
    'hiring': r'найм|нанять|ваканси|подбор\w* персонал',
    'employee': r'обязанност|инсайдер|комплаенс',
    'construction': r'строительств|стройк|подряд',
    'stock': r'запас\w* товар|складск\w* запас|закупк\w* товар',
    'deposit': r'вклад|депозит', 'trade': r'импорт|экспорт|валют',
}
NUMBER = r'(?P<number>\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)\s*(?P<scale>млн|тыс|миллион\w*|тысяч\w*)?\.?\s*(?P<currency>руб(?:лей|ля|ль|\.)?|₽|доллар\w*|\$|евро|€)'
MONEY_KEYS = {
    'budget': r'бюджет|располагаем|доступн\w* сумм',
    'reserve': r'резерв|неприкосновенн\w* остат',
    'unit_price': r'цен\w* (?:одн|за)|стоимост\w* (?:одн|машин|станк)|за (?:машину|станок|единицу)',
    'investment': r'инвестиц|вложени', 'revenue': r'выручк|доход', 'costs': r'расход|затрат',
}


def topic_for(text):
    lower = text.lower().replace('ё', 'е')
    return next((key for key, pattern in TOPICS.items() if re.search(pattern, lower)), None)


def extract(text, index):
    facts = {}
    lower = text.lower().replace('ё', 'е')
    # Unknown or conditional proposals are not accepted as agreed premises.
    for part in re.split(r'[;!?\n]|(?<=[.!?])\s+(?=[А-ЯЁA-Z])', text):
        sentence = part.lower().replace('ё', 'е')
        if re.search(r'допустим|предполож|если бы|например|возможно|гипотетич', sentence):
            continue
        money_matches = list(re.finditer(NUMBER, sentence))
        for match in money_matches:
            before = sentence[:match.start()]
            if re.search(r'после [^.]{0,30}резерв|за вычетом', before):
                continue
            candidates = [(m.start(), key) for key, pattern in MONEY_KEYS.items() for m in re.finditer(pattern, before)]
            if re.search(r'[-−]\s*$', before):
                continue
            if not candidates:
                continue
            key = max(candidates)[1]
            if len(before[max(candidates)[0]:]) > 85:
                continue
            value = Decimal(re.sub(r'\s', '', match['number']).replace(',', '.'))
            if match['scale']:
                value *= 1000000 if match['scale'].startswith(('млн', 'миллион')) else 1000
            unit = 'usd' if match['currency'].startswith(('доллар', '$')) else 'eur' if match['currency'].startswith(('евро', '€')) else 'rub'
            after = sentence[match.end():match.end() + 25]
            period = 'month' if re.search(r'в месяц|/мес|ежемесяч', sentence[max(0, match.start()-25):match.end()+25]) else 'year' if re.search(r'в год|/год|годов', after) else ''
            if re.search(r'не\s*$', before) or (key == 'unit_price' and re.search(r'за\s+\d+\s', before)):
                continue
            facts[key] = {'value': str(value), 'unit': unit, 'period': period, 'message': text, 'turn': index, 'provenance': 'user'}
    for key, pattern, unit in [
        ('quantity', r'(?:купить|покупаем|нужно|количество|закупаем|приобрести)\s*(\d+)\s*(?:машин|автомобил|станк|штук|единиц)', 'count'),
        ('duration', r'(?:срок|горизонт)\D{0,15}(\d+)\s*месяц', 'month'),
        ('loan_rate', r'(?:кредит|займ|ставк\w* по кредиту)\D{0,25}(\d+(?:[.,]\d+)?)\s*%', 'percent'),
        ('utilization', r'(?:загрузк\w*|занятост\w*)\D{0,15}(\d+(?:[.,]\d+)?)\s*%', 'percent')]:
        match = re.search(pattern, lower)
        if match:
            value = Decimal(match[1].replace(',', '.'))
            if unit != 'percent' or value <= 100:
                facts[key] = {'value': str(value), 'unit': unit, 'period': '', 'message': text, 'turn': index, 'provenance': 'user'}
    if re.search(r'все (?:машины|автомобили|станки) (?:заняты|загружены)|полная загрузка|загрузка полная', lower):
        facts['utilization_note'] = {'text': 'По вашим данным, имеющиеся мощности полностью заняты; наличие неудовлетворённого прибыльного спроса не установлено', 'message': text, 'turn': index, 'provenance': 'user'}
    if re.search(r'(?:есть|имеется|подтвержд[её]н)\w* [^.]{0,30}(?:неудовлетвор[её]нн\w* спрос|очередь заказов)|отказываем (?:клиентам|в заказах)', lower):
        facts['demand_note'] = {'text': 'По вашим данным, есть неудовлетворённый спрос; прибыльность дополнительных заказов требует оценки', 'message': text, 'turn': index, 'provenance': 'user'}
    role = re.search(r'я\s+(?:работаю\s+)?(?:как\s+)?(финансовый директор|директор|собственник|бухгалтер|казначей|сотрудник|руководитель)', lower)
    if role:
        facts['role'] = {'text': role[1], 'message': text, 'turn': index, 'provenance': 'user'}
    return facts


def task_memory(saved, dialogue, question, count):
    existing = saved.get('_chat', {}).get('memory', {})
    memory = copy.deepcopy(existing) if isinstance(existing, dict) else {}
    memory.setdefault('facts', {})
    memory.setdefault('topic', None)
    memory.setdefault('notes', [])
    processed = memory.get('processed_count', 0)
    start = max(0, count - len(dialogue))
    turns = [(start + i, row['question']) for i, row in enumerate(dialogue) if start + i >= processed]
    turns.append((count, question))
    for index, text in turns:
        lower = text.lower()
        reset = bool(re.search(r'сброс\w* (?:контекст|памят|услови)|забудь|начн[её]м (?:заново|сначала)', lower))
        topic = topic_for(text)
        changed = topic and memory['topic'] and topic != memory['topic']
        if reset or changed:
            memory = {'facts': {}, 'topic': None, 'notes': [], 'since': index}
        if topic:
            memory['topic'] = topic
            memory['goal'] = {'fleet': 'Решение по автопарку', 'equipment': 'Решение по оборудованию', 'hiring': 'Решение о найме', 'employee': 'Действия сотрудника', 'construction': 'Решение по строительному проекту', 'stock': 'Решение по запасам', 'deposit': 'Размещение средств', 'trade': 'Внешнеторговые расчёты'}[topic]
        memory['facts'].update(extract(text, index))
        # Only recent user statements, tagged as premises rather than facts of sources.
        memory['notes'] = (memory['notes'] + [{'text': text[:700], 'turn': index}])[-4:]
    memory['processed_count'] = count + 1
    return memory


def memory_presentation(memory):
    labels = {'budget': 'Ваш бюджет', 'reserve': 'Указанный вами резерв', 'unit_price': 'Указанная вами цена единицы',
              'investment': 'Указанные вами инвестиции', 'revenue': 'Сообщённая вами выручка', 'costs': 'Сообщённые вами расходы',
              'loan_rate': 'Сообщённая вами ставка по кредиту; предложение банка не подтверждено', 'quantity': 'Планируемое количество', 'duration': 'Ваш срок', 'utilization': 'Сообщённая вами загрузка'}
    units = {'rub': 'рублей', 'usd': 'долларов', 'eur': 'евро', 'count': 'единиц', 'month': 'месяцев', 'percent': '%'}
    facts = []
    for key, fact in memory.get('facts', {}).items():
        if 'value' in fact:
            period = {'month': ' в месяц', 'year': ' в год', 'day': ' в день'}.get(fact.get('period'), '')
            facts.append(f"{labels.get(key, 'По вашим данным')}: {fact['value']} {units.get(fact['unit'], '')}{period}")
        else:
            facts.append(fact['text'])
    return facts
