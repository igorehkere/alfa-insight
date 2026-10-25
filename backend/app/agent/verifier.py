"""Deterministic evidence checks, not a claim of complete semantic verification."""
from __future__ import annotations

import re
from decimal import Decimal
from datetime import date

from app.agent.errors import EvidenceError
from app.agent.language import ensure_human, result_texts
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
    ensure_human(*result_texts(result))
    if ((context.get('event_relevance') or {}).get('level') == 'none'
            and (result.why_relevant or result.risks or result.opportunities)):
        raise EvidenceError('Содержательная связь с компанией не установлена; убери персональные последствия и поясни отсутствие связи')
    registry, profile = context["evidence"], context["profile"]
    if not result.what_happened:
        raise EvidenceError("Нет подтверждённых фактов новости")
    primary_id = context["news_id"]
    if not any(primary_id in item.source_ids and
               normalized(item.text) in normalized(registry[primary_id]["text"])
               for item in result.what_happened):
        raise EvidenceError("Анализ должен содержать факт именно выбранной новости")
    if {source.id for source in result.sources} - registry.keys():
        raise EvidenceError("Источник не был получен инструментами")
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
    bank_items = [item for item in registry.values() if item['source']['type'] == 'bank']
    if bank_items and not any(source.type == 'bank' for source in result.sources):
        raise EvidenceError('Найден подходящий банковский фон. Используй поддерживаемое им утверждение с датой и настоящим источником; не выдавай прогноз за решение.')
    # Historical context referenced in interpretation must be available and dated.
    for sid, item in registry.items():
        if item['source']['type'] != 'bank':
            continue
        title = item['source']['title']
        sentences = [part.strip() for part in re.split(r'(?<=[.!?])\s+', item['text']) if len(part.strip()) >= 40]
        mentioned = title in result.ai_interpretation or any(part in result.ai_interpretation for part in sentences)
        if mentioned and sid not in {source.id for source in result.sources}:
            raise EvidenceError('Использованный банковский фон должен иметь настоящий источник в ответе')
        if mentioned and item.get('published_at', '') < context.get('as_of', ''):
            published = date.fromisoformat(item['published_at'])
            months = ('января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря')
            date_forms = [published.isoformat(), published.strftime('%d.%m.%Y'), f'{published.day} {months[published.month-1]} {published.year}']
            if not any(value in result.ai_interpretation for value in date_forms):
                raise EvidenceError('Укажи дату исторического банковского материала рядом с выводом')
    support = ' '.join(item['text'] + ' ' + str(item.get('published_at', '')) for item in registry.values())
    # Qualitative interpretation and actions may not smuggle new quantities around impacts.
    for text in [result.ai_interpretation, *result.suggested_actions, *[item.text for item in result.why_relevant]]:
        for claim in chat_claims(re.sub(r'(?m)^\s*\d+[.)]\s+', '', text)):
            if claim not in chat_claims(support):
                raise EvidenceError('Число или его смысл в интерпретации не подтверждены материалами')
    # IDs are not enough: assert every referenced record is actually in the registry.
    if used - registry.keys():
        raise EvidenceError("Неизвестное доказательство")


def chat_claims(text):
    """Conservative value/unit/subject matching; not general semantic entailment."""
    claims = []
    pattern = re.compile(r"(?P<value>[+−-]?(?:\d{1,3}(?:[ \u00a0\u202f]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?))"
                         r"[ \u00a0\u202f]*(?P<scale>млн|тыс|миллион\w*|тысяч\w*)?\.?[ \u00a0\u202f]*"
                         r"(?P<unit>₽|€|\$|%|процент\w*|руб(?:лей|ля|ль|\.)?|доллар\w*|евро|штук\w*|станк\w*|машин\w*|автомобил\w*|единиц\w*|год\w*|г\.|дн\w*|день|дней|месяц\w*|тонн\w*)?", re.I)
    for sentence in re.split(r"(?<=[.!?])\s+(?=[А-ЯЁA-Z])|\n", text):
        lower = sentence.lower().replace("ё", "е")
        for match in pattern.finditer(lower):
            value = Decimal(re.sub(r"[ \u00a0\u202f]", "", match["value"]).replace(",", ".").replace("−", "-"))
            scale = match["scale"] or ""
            unit = match["unit"] or ""
            if scale:
                value *= 1000000 if scale.startswith(("млн", "миллион")) else 1000
            if unit.startswith(("%", "процент")):
                unit = "percent"
            elif unit.startswith(("руб", "₽")):
                unit = "rub"
            elif unit.startswith(("доллар", "$")):
                unit = "usd"
            elif unit.startswith(("евро", "€")):
                unit = "eur"
            elif unit.startswith(("год", "г.")):
                unit = "year"
            elif unit.startswith(("дн", "день", "дней")):
                unit = "day"
            elif unit.startswith("месяц"):
                unit = "month"
            elif unit.startswith("тонн"):
                unit = "tonne"
            elif unit.startswith(("штук", "станк", "машин", "автомобил", "единиц")):
                unit = "count"
            # Percentage subjects matter: a key rate is not a loan offer or inflation.
            role = ""
            if unit == "percent":
                subjects = []
                before = lower[:match.start()]
                for subject, expression in (("key_rate", r"ключев\w* ставк"), ("inflation", r"инфляц|дефляц"),
                                            ("loan", r"кредит|займ|заем|заём"), ("deposit", r"вклад|депозит")):
                    subjects.extend((m.start(), subject) for m in re.finditer(expression, before))
                if subjects:
                    role = max(subjects)[1]
            claims.append((value, unit, role))
    return claims


def verify_answer(answer: AnswerResponse, context: dict) -> None:
    ensure_human(answer.answer, *answer.warnings)
    if set(answer.source_ids) - context["evidence"].keys():
        raise EvidenceError("Чат сослался на источник вне сохранённого контекста")
    if not answer.source_ids and not answer.warnings and not context.get("_task", {}).get("facts"):
        raise EvidenceError("Ответ без источников должен явно сообщать об ограничении")
    support_texts = [context["evidence"][s]["text"] for s in answer.source_ids]
    # Strip only line-initial list markers and the exact scoped company identifier.
    text = re.sub(r"(?m)^([ \t]*)\d+[.)][ \t]+", r"\1", answer.answer + "\n" + "\n".join(answer.warnings))
    company_id = context.get("company_id")
    if company_id:
        if company_id.isdigit():
            label = r"((?:компани[яию]|company|id)\s*(?:[:№=]\s*)?)"
            text = re.sub(label + re.escape(company_id) + r"(?![\w%]|\s*(?:%|руб|процент))", r"\1", text, flags=re.I)
        else:
            text = re.sub(r"(?<![\w])" + re.escape(company_id) + r"(?![\w])", "", text)
    allowed = {claim for source in support_texts for claim in chat_claims(source)}
    task = context.get('_task', {})
    user_facts = [{**fact, 'role': 'loan' if key == 'loan_rate' else ''} for key, fact in task.get('facts', {}).items() if 'value' in fact]
    calculations = context.get('_calculations', [])
    # User values retain their subject, currency, period, and message provenance.
    user_claims = {(Decimal(f['value']), f['unit'], f['role']) for f in user_facts if f.get('provenance') == 'user'}
    calculation_claims = {(Decimal(row['value']), row['unit'], '') for row in calculations}
    attributed = bool(re.search(r'по вашим|указанн\w* вами|сообщ[её]нн\w* вами|ваш\w* (?:бюджет|резерв|данн|услов)', text.lower()))
    for value, unit, role in chat_claims(text):
        from_user = attributed and (value, unit, role) in user_claims
        from_calculation = not role and (value, unit, '') in calculation_claims
        if not from_user and not from_calculation and not any(value == v and unit == u and (not role or role == r) for v, u, r in allowed):
            raise EvidenceError("Числа, единицы или смысл значения в ответе чата не подтверждены процитированными источниками")
    # Money cannot change its meaning or accounting period merely because the value matches.
    from app.agent.memory import extract
    for key, claim in extract(text, -1).items():
        if 'value' not in claim or key not in {'budget', 'reserve', 'revenue', 'costs', 'unit_price', 'investment', 'utilization', 'duration', 'quantity'}:
            continue
        matching = task.get('facts', {}).get(key)
        if attributed and matching and 'value' in matching:
            if (Decimal(claim['value']), claim['unit'], claim.get('period', '')) != (Decimal(matching['value']), matching['unit'], matching.get('period', '')):
                raise EvidenceError('Условие пользователя изменило значение, валюту или период')
        elif attributed and (Decimal(claim['value']), claim['unit'], '') in user_claims:
            raise EvidenceError('Пользовательская величина использована с другим назначением')
    # Forecasts cannot be relabelled as decisions or today's contract terms.
    for sentence in re.split(r'(?<=[.!?])\s+|\n', text):
        lower = sentence.lower()
        decision = re.search(r'(?:цб|банк россии|регулятор)[^.]{0,60}(сохранил|снизил|повысил|принял решение)|(?:сегодня|сейчас|текущая|действующая)[^.]{0,30}ставк', lower)
        if not decision or not chat_claims(sentence):
            continue
        claims = set(chat_claims(sentence))
        supported = False
        for source in support_texts:
            for original in re.split(r'(?<=[.!?])\s+|\n', source):
                original_lower = original.lower()
                if re.search(r'прогноз|ожида|предполага|возможно', original_lower):
                    continue
                action = decision[1]
                if action and action not in original_lower:
                    continue
                if claims <= set(chat_claims(original)):
                    supported = True
        if not supported:
            raise EvidenceError('Прогноз или пользовательское условие не подтверждают факт решения либо текущую ставку')
    # Bare-number matching never permits a promise or an invented legal duty.
    for sentence in re.split(r"(?<=[.!?])\s+|\n", text):
        lower = sentence.lower()
        if re.search(r"(?:банк|кредит)[^.]*?(?:одобрит|одобрен|выдаст|гарантирует|предлагает)|(?:вы|сотрудник|компания|работодатель) (?:обязан|должн)|у вас (?:есть )?(?:долг|просрочка)", lower):
            if not any(normalized(sentence) in normalized(source) for source in support_texts):
                raise EvidenceError("Обещание, обязанность или долг не подтверждены первичной выдержкой")
