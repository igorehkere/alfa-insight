"""Meaning-based profile presentation and a fail-closed public prose boundary.

Metadata and original evidence are never rewritten by this module.
"""
import re

from app.agent.errors import EvidenceError

PRODUCTS = {
    'settlement': 'расчётное обслуживание', 'loan': 'кредитный продукт',
    'leasing': 'лизинг', 'guarantee': 'банковская гарантия',
    'deposit': 'депозит', 'payroll': 'зарплатный проект',
    'acquiring': 'эквайринг', 'credit_card': 'кредитная карта',
    'factoring': 'факторинг',
}
TECHNICAL = re.compile(
    r'\b\d{2}\.\d{1,2}\.\d{1,2}\b|'
    r'\b[a-zA-Z][a-zA-Z0-9]*_[a-zA-Z0-9_]+\b|'
    r'\b[a-zA-Z][\w]*\.[a-zA-Z_][\w.]*\b|'
    r'\b(?:true|false|null|none|not_found|bank_products|source_ids|verifier|context|'
    r'answer_question|analyze|run_analysis|search_bank_analytics|industry|features|leasing|settlement|guarantee|payroll|loan|has_customs_data)\b|'
    r'\b\w+\s*=\s*(?:true|false|null|none|not_found)\b|'
    r'\b(?:SELECT\s+.+?\s+FROM|INSERT\s+INTO|UPDATE\s+\w+\s+SET|DROP\s+TABLE)\b|'
    r'\{\s*["\'][\w]+["\']\s*:|```|'
    r'(?:sk-[a-zA-Z0-9-]{8,}|Bearer\s+\S+|/app/|/backend/|postgresql://)|'
    r'\b[A-Z][A-Za-z]*(?:Error|Exception)\b|\b[a-z_]+\(\)|'
    r'\b(?:system prompt|системн\w* промпт|Traceback)\b', re.I | re.S)


def ensure_human(*texts):
    for text in texts:
        if TECHNICAL.search(str(text)):
            raise EvidenceError('В тексте есть служебная запись. Объясни её проверенный смысл обычными русскими словами; неизвестный смысл не расшифровывай. Метаданные и точные цитаты не подменяй.')


def profile_facts(profile):
    """Only known semantics; an unknown flag never receives an invented label."""
    features = profile.get('features') or {}
    facts = []
    industry = features.get('industry_name') or profile.get('industry')
    if industry and not TECHNICAL.search(str(industry)):
        facts.append('Указанный вид деятельности: ' + str(industry))
    region = features.get('region') or profile.get('region')
    if region and not TECHNICAL.search(str(region)):
        facts.append('Регион компании: ' + str(region))
    for key, value in (features.get('bank_products') or {}).items():
        label = PRODUCTS.get(key)
        if not label:
            continue
        if value is True:
            facts.append(f'В профиле указан {label}; условия договора неизвестны')
        elif value is False:
            facts.append(f'В доступном профиле нет отметки о продукте «{label}»; это не доказывает отсутствие обязательств в других банках')
    if features.get('has_customs_data') is True:
        facts.append('Есть сведения о внешнеторговой деятельности; направление и валюта расчётов не установлены')
    countries = features.get('customs_countries')
    if countries:
        safe = [str(c) for c in countries if not TECHNICAL.search(str(c))]
        if safe:
            facts.append('В сведениях о внешней торговле указаны страны: ' + ', '.join(safe))
    return facts


def compact_warnings(rows):
    result, seen = [], set()
    categories = (
        ('summary', r'кратк\w* описан|полный текст.*(?:не провер|недоступ|не анализ)|не полный текст'),
        ('bank', r'(?:позици|комментар).*банк.*не найден'),
        ('generation', r'не удалось.*(?:ответ|объяснен)|резервн\w* ответ|генераци.*недоступ'),
    )
    for text in rows:
        ensure_human(text)
        key = next((name for name, pattern in categories if re.search(pattern, text, re.I)), ' '.join(text.lower().split()))
        if key not in seen:
            result.append(text)
            seen.add(key)
    return result


def result_texts(result):
    yield result.ai_interpretation
    yield from result.suggested_actions
    yield from result.warnings
    for item in [*result.what_happened, *result.why_relevant, *result.risks, *result.opportunities]:
        yield item.text
    if result.bank_position.text:
        yield result.bank_position.text
