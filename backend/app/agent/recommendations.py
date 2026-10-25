"""Explainable event-to-business ranking; retrieval similarity is never proof."""
from __future__ import annotations

import asyncio
import re
from datetime import datetime, timezone
from decimal import Decimal
from time import monotonic

from app.agent.tools import validate_profile
from app.rag.search import topics
from app.agent.relevance import RelevanceEngine, candidate_news

GENERIC = {'деятел', 'управл', 'услуг', 'торгов', 'рознич', 'оптов', 'проч', 'собств', 'аренд', 'органи', 'област', 'компан', 'предпр', 'вспомо', 'специа', 'проект'}
# The longest matching code takes precedence. Text only supplements an unknown code.
INDUSTRIES = {
    'rental': (('77.11', '77.12'), ('прокат', 'аренда автомобилей', 'аренда легковых', 'лизинг автомобилей', 'аренда и лизинг легковых'), r'автомобил|автопарк|авторын|автопром|автолизинг|автоваз|осаг|каско'),
    'auto': (('45', '29'), ('автомобил', 'автозапчаст', 'автодетал'), r'автомобил|автозапчаст|авторын|автопром|автодетал'),
    'engineering': (('71.12', '71.11'), ('инженерные изыскания', 'инженерно-техническое проектирование'), r'инженерн\w* изыскан|проектирован\w* здан|строительн\w* контрол'),
    'construction': (('41', '42', '43'), ('строительств', 'строительные работы'), r'строител|стройматериал|цемент|подрядчик'),
    'property': (('68',), ('недвижим', 'управление жильем'), r'недвижим|арендн\w* плат|коммерческ\w* помещен|жилищн'),
    'data': (('63.1', '63.11', '63.12'), ('обработка данных', 'датацентр', 'центры обработки данных'), r'обработк\w* данных|дата.?центр|центр\w* обработки данных|облачн\w* сервис'),
    'games': (('58.21',), ('видеоигр', 'компьютерных игр'), r'видеоигр|компьютерн\w* игр|игров\w* студи'),
    'it': (('62',), ('программного обеспечения', 'информационных технологий'), r'программ\w* обеспечен|разработчик\w* по|кибербезопас|ит-компани|it\b|цифров\w* сервис'),
    'research': (('72',), ('научные исследования', 'научных исследований'), r'научн\w* исследован|лаборатор|исследовательск|научн\w* грант'),
    'beverages': (('11', '47.25'), ('напитк', 'алкогол', 'винодел'), r'напитк|алкогол|винодел|вин[аоуы]?\b|винн|пив|игрист|акциз'),
    'metals': (('24',), ('металлург', 'сталелит'), r'металлург|сталь|сталелит|сталевар'),
    'agriculture': (('01',), ('сельскохозяй', 'аграр', 'выращивание'), r'сельскохозяй|аграр|зерн|урожай|посевн'),
    'transport': (('49', '50', '51', '52'), ('грузоперевоз', 'грузовых перевозок', 'логистик'), r'грузоперевоз|грузов\w* перевоз|логист|железнодорож|ржд|склад'),
    'industrial': (('28', '33'), ('производство оборудования', 'производство машин'), r'(?:промышленн|буров)\w* оборудован|станк|машиностро|производств\w* машин'),
    'equipment_trade': (('46.6', '46.61', '46.62', '46.63', '46.64', '46.65', '46.66', '46.69'), ('торговля машинами', 'торговля оборудованием'), r'промышленн\w* оборудован|станк|поставк\w* оборудован|импорт\w* оборудован'),
    'trade': (('47', '46'), ('розничная торговля', 'оптовая торговля', 'магазин'), r'розничн\w* торгов|оптов\w* торгов|магазин|ритейл|торгов\w* сет'),
    'services': (('96',), ('бытовые услуги', 'салон красоты', 'парикмахер'), r'бытов\w* услуг|салон\w* красот|парикмахер'),
}


def industry_terms(text):
    words = re.findall(r'[а-яa-z]+', text.lower().replace('ё', 'е'))
    return {word[:6] for word in words if len(word) >= 4 and not any(word.startswith(root) for root in GENERIC)}


def classify_news(news):
    text = f"{news.get('title', '')} {news.get('body', '')}".lower().replace('ё', 'е')
    tags = topics(text) - {'rates', 'currency', 'trade'}
    if re.search(r'памятн\w* монет|монет\w* .*номинал|выпуск\w* .*монет', text):
        return ['commemorative_coins']
    retail = bool(re.search(r'вклад\w* .*физическ|депозит\w* .*населени|вклад\w* .*населени|средств\w* .*физическ', text))
    targeted = bool(re.search(r'продавц\w* маркетплейс|для (?:населения|граждан|физических лиц)|ипотек|потребительск|кредит\w* наличн|кредитн\w* карт', text))
    if retail:
        tags.add('retail_deposits')
    if not retail and not targeted and re.search(r'ключев\w* ставк|стоимост\w* финансирован|услови\w* кредитован\w* (?:компани|бизнес|предприят)|корпоративн\w* кредит[^.]{0,35}(?:подорож|ставк|услов|стоим)|ставк\w* по кредит', text):
        tags.add('rates')
    if re.search(r'валютн\w* (?:рын|курс|расчет|огранич|операц)|курс\w* (?:рубл|доллар|евро|юан)|(?:ослаблен|укреплен)\w* рубл', text):
        tags.add('currency')
    if re.search(r'импорт|экспорт|таможенн\w* (?:пошлин|правил|огранич)', text):
        tags.add('trade')
    if re.search(r'рын\w* труда|заработн\w* плат|зарплат|дефицит\w* кадр', text):
        tags.add('labor')
    if re.search(r'(?:огранич|приостан|нов\w* правил)\w* [^.]*расчет|платежн\w* (?:огранич|систем)|расчетн\w* (?:огранич|счет)', text):
        tags.add('settlements')
    return sorted(tags)


def business_models(profile):
    features = profile.get('features') or {}
    code = str(features.get('industry_code') or profile.get('industry_code') or '')
    industry = str(features.get('industry_name') or profile.get('industry') or '').lower().replace('ё', 'е')
    matches = [(len(prefix), name) for name, (prefixes, _, _) in INDUSTRIES.items() for prefix in prefixes
               if re.match('^' + re.escape(prefix) + r'(?:\.|$)', code)]
    if matches:
        return {max(matches)[1]}
    return {name for name, (_, words, _) in INDUSTRIES.items() if any(re.search(r'(?<!\w)' + re.escape(w), industry) for w in words)}


def event_key(news):
    title = news.get('title', '').lower().replace('ё', 'е')
    if not re.search(r'ключев\w* ставк', title) or re.search(r'прогноз|объясн|рассматри|ожида', title):
        return None
    action = next((name for name, pattern in [('hold', r'сохранил|сохранена|решение сохранить|оставил'),
                                              ('cut', r'снизил|снижена'), ('raise', r'повысил|повышена')]
                   if re.search(pattern, title)), None)
    value = re.search(r'(\d+(?:[.,]\d+)?)\s*%', title + ' ' + news.get('body', '')[:300])
    day = str(news.get('published_at') or '')[:10]
    if action and value and day:
        return ('policy_rate', action, str(Decimal(value[1].replace(',', '.')).normalize()), day)
    return None


def _card(news, score, reasons, *, assessment=None):
    level = assessment['level'] if assessment else ('medium' if score >= 10 else 'low')
    return {'id': str(news['id']), 'title': news.get('title', ''),
            'source': news.get('source', 'unknown'), 'source_url': news.get('source_url'),
            'published_at': news.get('published_at'), 'topic_tags': classify_news(news),
            'relevance_score': score, 'relevance_reasons': reasons, 'relevance_level': level,
            'relevance_method': 'llm' if assessment else 'local',
            'event_analysis': assessment,
            '_body_size': len(news.get('body', '')), '_event_key': event_key(news)}


def rank_news(profile, news_items, limit=10, *, assessments=None):
    if not 1 <= limit <= 100:
        raise ValueError('limit должен быть от 1 до 100')
    features = profile.get('features') or {}
    products = features.get('bank_products') or {}
    models = business_models(profile)
    results = []
    for news in news_items:
        identifier = str(news['id'])
        if assessments is not None and identifier in assessments:
            assessment = assessments[identifier]
            if assessment is None:
                continue
            explanation = assessment['mechanism']
            if assessment['conditions']:
                explanation += ' Требует проверки: ' + '; '.join(assessment['conditions'])
            # Igor's current UI labels scores >=20 as high. Keep medium/low below it.
            score = {'high': 80, 'medium': 15, 'low': 5}[assessment['level']]
            # The frontend concatenates reason texts: one explanation per event,
            # with all supporting paths retained in event_analysis.fields.
            reasons = [{'company_field': assessment['fields'][0], 'reason': explanation}]
            results.append(_card(news, score, reasons, assessment=assessment))
            continue
        title = news.get('title', '')
        # Main event: title and lead, rather than a passing mention deep in the article.
        lead = re.split(r'(?<=[.!?])\s+', news.get('body', ''))[0][:650]
        main = (title + ' ' + lead).lower().replace('ё', 'е')
        title_lower = title.lower().replace('ё', 'е')
        title_sectors = {name for name, (_, _, pattern) in INDUSTRIES.items()
                         if re.search(r'(?<!\w)(?:' + pattern + ')', title_lower)}
        place = re.search(r'^(?:В|На)\s+([А-ЯЁ][а-яё-]+(?:\s+(?:области|округе|крае))?)', title)
        region = str(features.get('region') or profile.get('region') or '').lower().replace('ё', 'е')
        local_restriction = (place and place[1].lower() not in {'россии', 'рф', 'мире', 'стране'}
                             and re.search(r'отмен|введ|огранич|запрет|субсиди|поддержк', main)
                             and not (region and (place[1].lower() in region or region in main)))
        tags = set(classify_news({'title': title, 'body': lead}))
        if 'commemorative_coins' in tags:
            continue
        score, reasons = 0, []

        def add(points, field, reason):
            nonlocal score
            score += points
            reasons.append({'company_field': field, 'reason': reason})

        virtual = bool(re.search(r'видеоигр|компьютерн\w* игр|виртуальн\w* (?:здани|строител|город)', main))
        physical = bool(re.search(r'(?:начал|ведет|ведут|подрядчик|разрешени|контракт|реальн)\w* [^.]{0,60}(?:строител|офис|здани|объект)|(?:строительств|строит|возведен)\w* [^.]{0,70}(?:офис|завод)|(?:разрешени|требован)\w* [^.]{0,40}(?:строй|строител|материал)', main))
        for model in sorted(models):
            pattern = INDUSTRIES[model][2]
            if local_restriction:
                continue
            # An explicit different market in the headline takes precedence over a
            # passing example in the description. Overlapping markets remain allowed.
            related = {'rental': {'rental', 'auto'}, 'equipment_trade': {'equipment_trade', 'industrial'},
                       'industrial': {'industrial', 'equipment_trade'}, 'construction': {'construction', 'engineering'},
                       'engineering': {'engineering', 'construction'}}.get(model, {model})
            if title_sectors and not (title_sectors & related):
                continue
            if not re.search(r'(?<!\w)(?:' + pattern + ')', main):
                continue
            if model in {'construction', 'property'} and virtual and not physical:
                continue
            if model in {'construction', 'property'} and re.search(r'судостро|супертраулер|верф|строительств\w* [^.]{0,35}(?:судов|корабл|танкер)', main):
                continue
            if model == 'data' and 'retail_deposits' in tags:
                continue
            field = 'features.industry_name' if features.get('industry_name') else 'industry'
            if not (features.get('industry_name') or profile.get('industry')):
                field = 'features.industry_code' if features.get('industry_code') else 'industry_code'
            label = {'rental': 'стоимость и доступность автомобилей для аренды', 'construction': 'условия реального строительства',
                     'data': 'работу с данными и вычислительной инфраструктурой', 'transport': 'перевозку и хранение грузов'}.get(model, 'рынок указанного вида деятельности')
            add(12, field, f'Событие может затронуть {label}; прямое влияние на компанию пока не подтверждено')
            break
        if 'rates' in tags:
            if products.get('loan') is True:
                add(8, 'features.bank_products.loan', 'В профиле указан кредитный продукт: сравните условия нового финансирования; долг и ставка действующего договора неизвестны')
            elif products.get('leasing') is True:
                add(8, 'features.bank_products.leasing', 'В профиле указан лизинг: изменение стоимости нового финансирования важно для сравнения предложений; изменение действующих платежей не установлено')
            elif products.get('deposit') is True:
                add(8, 'features.bank_products.deposit', 'В профиле указан депозит: событие важно для сравнения условий размещения средств; текущие условия неизвестны')
        # A payroll product does not establish couriers, delivery or exposure to
        # another sector's wage dynamics. Only explicit payroll-rule events qualify.
        if (products.get('payroll') is True and not local_restriction
                and re.search(r'зарплатн\w* проект|(?:правил|порядок|обязанност)\w* [^.]{0,50}(?:выплат\w* зарплат|оплат\w* труда)', main)):
            add(5, 'features.bank_products.payroll', 'Возможны изменения порядка зарплатных выплат; круг работодателей и применимость правил требуют проверки')
        for tag, product, reason in [
            ('settlements', 'settlement', 'Изменения правил расчётов могут влиять на расчётное обслуживание; применимость ограничений требует проверки')]:
            if tag in tags and products.get(product) is True:
                add(8, 'features.bank_products.' + product, reason)
        if tags & {'trade', 'currency'} and features.get('has_customs_data') is True:
            add(8, 'features.has_customs_data', 'Есть сведения о внешней торговле: изменения валютных расчётов или таможенных условий могут затронуть операции; направление и валюта неизвестны')
        region = features.get('region') or profile.get('region')
        if region and str(region).lower() in main and re.search(r'поддержк\w* бизнеса|огранич\w* [^.]*предприят|региональн\w* (?:налог|льгот)', main) and not re.search(r'монетн\w* двор|юридическ\w* адрес', main):
            add(10, 'features.region' if features.get('region') else 'region', 'В регионе компании меняются условия ведения бизнеса; применимость программы требует проверки')
        if not score:
            continue
        results.append(_card(news, min(15, score), reasons))

    def published(row):
        try:
            value = datetime.fromisoformat(row['published_at'].replace('Z', '+00:00'))
            return value.replace(tzinfo=value.tzinfo or timezone.utc).timestamp()
        except (ValueError, TypeError, AttributeError):
            return 0

    # Prefer informative copies; near-duplicates must preserve numbers and event date.
    selected = []
    for row in sorted(results, key=lambda r: (-r['relevance_score'], -r['_body_size'])):
        words = industry_terms(row['title'])
        duplicate = False
        for old in selected:
            other = industry_terms(old['title'])
            same_title = re.sub(r'\W+', '', row['title'].lower()) == re.sub(r'\W+', '', old['title'].lower())
            near = len(words & other) / max(1, len(words | other)) >= .82 and len(words & other) >= 4
            same_numbers = re.findall(r'\d+(?:[.,]\d+)?', row['title']) == re.findall(r'\d+(?:[.,]\d+)?', old['title'])
            same_day = str(row['published_at'])[:10] == str(old['published_at'])[:10]
            if (row['_event_key'] and row['_event_key'] == old['_event_key']) or (row['source_url'] and row['source_url'] == old['source_url']) or ((same_title or near) and same_numbers and same_day):
                duplicate = True
                break
        if not duplicate:
            selected.append(row)
    for row in selected:
        row.pop('_body_size')
        row.pop('_event_key')
    selected.sort(key=lambda r: (-r['relevance_score'], -published(r), r['id']))
    return selected[:limit]


async def recommend_news(company_id, tools, limit=10):
    if not 1 <= limit <= 100:
        raise ValueError('limit должен быть от 1 до 100')
    profile = validate_profile(await tools.get_company_profile(company_id), company_id)
    news, started = [], monotonic()
    paged = getattr(tools, 'list_news_page', None)
    if callable(paged):
        for offset in range(0, 600, 200):
            if monotonic() - started >= 5:
                break
            try:
                rows = await asyncio.wait_for(paged(limit=200, offset=offset), max(.01, 5 - (monotonic() - started)))
            except TimeoutError:
                break
            news.extend(rows)
            if len(rows) < 200:
                break
    else:
        news = await tools.list_news(limit=200)
    engine = getattr(tools, 'relevance_engine', None) or RelevanceEngine(getattr(tools, 'llm', None))
    if not engine.enabled or not news:
        return {'company_id': company_id, 'items': rank_news(profile, news, limit)}
    context = engine.cached_context(profile)
    baseline = rank_news(profile, news, 100)
    candidates = candidate_news(profile, context, news, baseline)
    assessments = engine.cached_assessments(context, candidates)
    complete = engine.has_cached_context(profile) and len(assessments) == len(candidates)
    # The runtime owns background work and closes it before the shared LLM client.
    # An ad-hoc caller without a runtime gets local/cached results without leaking tasks.
    shared_engine = getattr(tools, 'relevance_engine', None) is engine
    pending = (not complete and shared_engine and engine.schedule_refresh(profile, news, baseline))
    # Keep all local matches eligible while some candidates are still unassessed.
    items = rank_news(profile, candidates if complete else news, limit, assessments=assessments)
    return {'company_id': company_id, 'items': items,
            'ranking_status': 'ready' if complete else 'refreshing' if pending else 'partial'}
