"""Optional local retrieval. Additional evidence needs durable, scoped storage."""
import copy
import re
from datetime import date

from app.agent.errors import AgentError, InvalidInput
from app.agent.language import ensure_human
from app.agent.recommendations import industry_terms

GENERIC_QUESTION = {'провер', 'сравни', 'подроб', 'расска', 'сделат', 'делать', 'вариан', 'помоги', 'компан', 'влияни', 'новост', 'почему', 'важно', 'какие', 'услови'}


async def expand_evidence(insight, context, question, tools):
    search = getattr(tools, 'search_chat_evidence', None)
    if tools.llm is None:
        return {}
    terms = industry_terms(question) - GENERIC_QUESTION
    if len(terms) < 2:
        return {}
    covered = set().union(*(industry_terms(row['text']) for row in context['evidence'].values())) if context['evidence'] else set()
    if len(terms & covered) >= max(2, len(terms) // 2):
        return {}
    from app.agent.chat import evidence_excerpt
    from app.agent.evidence import bank_evidence_gap
    original_ids = {s['id'] for s in insight['result']['sources']}
    additions = {}
    # Already attributed sources survive restart without changing the public snapshot.
    if getattr(tools, '_can_save_chat', False):
        stored = insight['context'].get('_chat', {}).get('evidence', {})
        for sid, item in stored.items():
            if len(context['evidence']) >= 6:
                break
            if len(terms & industry_terms(item.get('text', ''))) < 2:
                continue
            if item.get('origin') != 'bank' or item.get('reviewed') is not True:
                continue
            try:
                ensure_human(item['text'], item['source']['title'])
            except (AgentError, KeyError):
                continue
            if len(item['text']) <= 1800 and sum(len(e['text']) for e in context['evidence'].values()) + len(item['text']) <= 12000:
                additions[sid] = copy.deepcopy(item)
                context['evidence'][sid] = copy.deepcopy(item)
    if additions:
        return additions
    if not callable(search):
        return {}
    as_of = date.fromisoformat(str(context.get('as_of') or date.today())[:10])
    queries = [question[:500], (' '.join(sorted(terms)) + ' ' + str(context['news'].get('title') or ''))[:500]]
    for query in dict.fromkeys(queries):
        try:
            rows = await search(query, as_of=as_of, limit=3)
        except InvalidInput:
            raise
        except (AgentError, OSError, ValueError, RuntimeError):
            continue
        for row in rows[:3]:
            try:
                sid = 'bank:' + row['chunk_id']
                if sid in context['evidence']:
                    continue
                if sid not in original_ids and not getattr(tools, '_can_save_chat', False):
                    continue
                if bank_evidence_gap({'title': question, 'body': ''}, row, as_of):
                    continue
                if not row['document_title'] or not row['document_id'] or not isinstance(row['page_number'], int) or row['page_number'] < 1:
                    continue
                sentences = re.split(r'(?<=[.!?])\s+(?=[А-ЯЁA-Z«])', row['text'])
                anchors = sorted(sentences, key=lambda s: -len(terms & industry_terms(s)))[:1]
                text = evidence_excerpt(row['text'], anchors, 1600)
                if not text:
                    continue
                ensure_human(text, row['document_title'])
                source = {'id': sid, 'type': 'bank', 'title': row['document_title'], 'url': row.get('source_url'),
                          'document_id': row['document_id'], 'document_title': row['document_title'], 'page_number': row['page_number']}
                # An existing ID must refer to exactly the original page and metadata.
                original = next((s for s in insight['result']['sources'] if s['id'] == sid), None)
                if original is not None and source != original:
                    continue
                item = {'source': source, 'text': text, 'published_at': row['published_at'], 'origin': 'bank', 'reviewed': True}
                if len(context['evidence']) < 6 and sum(len(e['text']) for e in context['evidence'].values()) + len(text) <= 12000:
                    context['evidence'][sid] = item
                    if sid not in original_ids:
                        additions[sid] = copy.deepcopy(item)
            except (KeyError, TypeError, ValueError, AgentError):
                continue
    return additions


def attribute_additions(answer, additions, insight):
    used = [sid for sid in answer.source_ids if sid in additions]
    from app.agent.chat import evidence_excerpt
    for sid in used:
        item = additions[sid]
        source = item['source']
        quote = evidence_excerpt(item['text'], budget=1700)
        answer.answer += (f"\n\nИсторический материал Альфа-Банка «{source['title']}», "
                          f"{item['published_at']}, страница {source['page_number']}: «{quote}»")
    allowed = {s['id'] for s in insight['result']['sources']}
    answer.source_ids = [sid for sid in answer.source_ids if sid in allowed]
    object.__setattr__(answer, '_used_additions', used)
    ensure_human(answer.answer)
