"""Short chat with attributed user premises, bounded memory and an honest fallback."""
import asyncio
import copy
import logging
import re
from time import perf_counter

from pydantic import Field, ValidationError

from app.agent.errors import AgentUnavailable, EvidenceError, InvalidInput
from app.agent.schema import AnswerResponse, AskRequest, NonEmptyText
from app.agent.state import AgentLimits, AgentState
from app.agent.verifier import normalized, verify_answer

CHAT_CYCLE = 3
CHAT_OUTPUT_TOKENS = 650
logger = logging.getLogger(__name__)


class ChatDraft(AnswerResponse):
    answer: NonEmptyText = Field(max_length=600)
    user_context: NonEmptyText | None = Field(default=None, max_length=180)


def recent_dialogue(history=None, turn_count=None):
    history = history or []
    count = len(history) if turn_count is None else turn_count
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise InvalidInput("turn_count должен быть неотрицательным числом")
    keep = count % CHAT_CYCLE
    result = []
    for row in history[-keep:] if keep else []:
        question = row.get("question", "")
        answer = row.get("response", {}).get("answer", "")
        if isinstance(question, str) and isinstance(answer, str):
            result.append({"question": question[:600], "answer": answer[:700]})
    return result, count


def verified_answer(draft, context, question, *, previous_questions=()):
    answer = AnswerResponse(**draft.model_dump(exclude={"user_context"}))
    verify_answer(answer, context)
    user_topic = " ".join((question, *previous_questions)).lower()
    news_topic = " ".join((str(context.get("news", {}).get("title") or ""),
                           context.get("evidence", {}).get(context.get("news_id"), {}).get("text", ""))).lower()
    financing = r"кредит|лизинг|финансирован|оборудован|станк"
    if re.search(financing, answer.answer.lower()) and not re.search(financing, user_topic + " " + news_topic):
        raise EvidenceError("Совет о финансировании не связан с вопросом и выбранной новостью")
    if draft.user_context:
        if not any(normalized(draft.user_context) in normalized(q) for q in (question, *previous_questions)):
            raise EvidenceError("user_context должен быть дословной цитатой пользовательского вопроса без новых данных")
        answer.answer = f"По вашим словам: «{draft.user_context}»\n\n{answer.answer}"
        answer.warnings.append("Условия из вашего вопроса сообщены вами и не подтверждены новостями или документами банка.")
    return answer


def general_answer(question, *, unavailable=False):
    answer = AnswerResponse(
        answer="Данных для адресного вывода недостаточно. Могу составить перечень проверок по вашей задаче. "
               "Какое решение вы принимаете и каких сведений вам не хватает?",
        source_ids=[], warnings=["Общие следующие действия; это не позиция банка и не подтверждённый вывод по компании."])
    if unavailable:
        answer.warnings.append("LLM недоступна; показан резервный ответ без генерации.")
    return answer


def evidence_excerpt(text, anchors=(), budget=1800):
    """Keep a contiguous window of complete sentences, including quote conditions."""
    if len(text) <= budget:
        return text
    boundaries = [0, *[m.end() for m in re.finditer(r'(?<=[.!?])\s+(?=[А-ЯЁA-Z«])|\n\s*\n', text)], len(text)]
    target = next((text.find(a) for a in anchors if a and a in text), 0)
    index = max(i for i, start in enumerate(boundaries[:-1]) if start <= target)
    start, end = boundaries[index:index + 2]
    if end - start > budget:
        return ""  # Never turn a partial sentence into evidence.
    # Include the preceding condition/negation where it fits, then following sentences.
    if index and end - boundaries[index - 1] <= budget:
        index -= 1
        start = boundaries[index]
    while index + 2 < len(boundaries) and boundaries[index + 2] - start <= budget:
        index += 1
        end = boundaries[index + 1]
    return text[start:end].strip()


def contextual_answer(context, question, dialogue, reason):
    """Local help uses user intent and the news topic, never rejected AI claims."""
    user_questions = [row["question"] for row in dialogue] + [question]
    groups = {
        "equipment": ("станк", "оборудован", "закуп", "лизинг", "финансирован", "кредит"),
        "employee": ("сотрудник", "работник", "обязанност", "комплаенс", "инсайдер"),
        "deposit": ("вклад", "депозит"),
        "trade": ("валют", "импорт", "экспорт", "расчёт", "расчет"),
        "rates": ("ставк",),
    }
    news = str(context.get("news", {}).get("title") or "").lower()
    primary = context.get("evidence", {}).get(context["news_id"], {}).get("text", "").lower()
    topic = None
    for text in [*reversed(user_questions), news + " " + primary]:
        topic = next((name for name, roots in groups.items() if any(root in text.lower() for root in roots)), None)
        if topic:
            break
    if topic == "employee":
        advice = ("Для действий сотрудника важны его обязанности и доступ к информации. "
                  "Сверьте рабочую ситуацию с внутренними правилами и уточните её у ответственного за комплаенс. "
                  "Могу составить вопросы для такой проверки. Какова ваша роль и какое действие вызывает сомнение?")
    elif topic == "equipment":
        advice = ("Сравните предложения рынка по кредиту, лизингу и поэтапной закупке, если задача связана с оборудованием. "
                  "Проверьте полную стоимость, сроки поставки и посильные платежи. "
                  "Могу составить таблицу сравнения. Что важнее для вашей задачи: срок запуска или размер платежа?")
    elif topic == "deposit":
        advice = ("Сначала уточните срок размещения средств и потребность в досрочном доступе к ним. "
                  "Могу составить перечень условий для сравнения вкладов. Какие средства и на какой срок вы рассматриваете?")
    elif topic == "trade":
        advice = ("Для оценки расчётов нужны валюта, направление платежа и условия договора. "
                  "Проверьте их с обслуживающим банком. Могу подготовить список вопросов. Какой платёж вы планируете?")
    elif topic == "rates":
        advice = ("Материал о ставке не определяет условия конкретного договора. "
                  "Сверьте действующие условия и дату публикации перед решением. "
                  "Могу помочь сравнить сообщённые варианты. Вы оцениваете заём или размещение средств?")
    else:
        advice = ("Сначала определите, какое решение зависит от этой новости, и проверьте относящиеся к нему условия. "
                  "Могу составить перечень вопросов по выбранной публикации. Какую часть раскрыть: событие, влияние на работу или следующий шаг?")
    answer = general_answer(question)
    answer.answer = "Подтверждения для точного вывода недостаточно. " + advice
    explanations = {
        "no_evidence": "В сохранённом контексте нет достаточных выдержек; показана общая помощь.",
        "rejected": "Черновики отклонены проверкой доказательств; показан резервный ответ без отвергнутых утверждений.",
        "unavailable": "LLM недоступна; показан резервный ответ без генерации.",
        "timeout": "Истекло время ожидания LLM; показан резервный ответ без генерации.",
    }
    answer.warnings.append(explanations[reason])
    return answer


def chat_context(insight):
    saved = insight["context"]
    ids = {source["id"] for source in insight["result"]["sources"]}
    # Prior assistant replies never become evidence. Bound the source text too.
    rows = [(sid, item) for sid, item in saved.get("evidence", {}).items() if sid in ids]
    rows.sort(key=lambda row: (row[0] != saved["news_id"], row[1].get("source", {}).get("type") != "bank", row[0]))
    evidence = {}
    for sid, item in rows[:3]:
        statements = [*insight["result"].get("what_happened", []), insight["result"].get("bank_position", {})]
        anchors = [row.get("text") for row in statements if sid in row.get("source_ids", [])]
        excerpt = evidence_excerpt(item.get("text", ""), anchors)
        if excerpt:
            evidence[sid] = copy.deepcopy(item)
            evidence[sid]["text"] = excerpt
            evidence[sid]["chat_excerpt_limited"] = excerpt != item.get("text", "")
    profile = saved.get("profile", {})
    return {"company_id": saved["company_id"], "news_id": saved["news_id"], "as_of": saved.get("as_of"),
            "profile": {key: copy.deepcopy(profile[key]) for key in ("company_id", "industry", "industry_code", "region", "features") if key in profile},
            "news": {key: saved.get("news", {}).get(key) for key in ("id", "title", "published_at", "provenance", "body_is_summary")},
            "evidence": evidence}


async def answer_question(insight, question, tools, *, history=None, turn_count=None, limits=None):
    from app.agent.service import complete_json
    try:
        question = AskRequest(question=question).question
    except ValidationError:
        raise InvalidInput("Вопрос должен содержать от 1 до 2000 символов") from None
    if len(question) > 2000:
        raise InvalidInput("Вопрос длиннее 2000 символов")
    saved = insight.get("context")
    if not saved or saved.get("company_id") != insight.get("company_id") or saved.get("news_id") != insight.get("news_id"):
        raise InvalidInput("Для чата нужен сохранённый контекст этого анализа")
    if getattr(tools, "company_id", None) != saved["company_id"]:
        raise InvalidInput("Чат относится к другой компании")
    dialogue, count = recent_dialogue(history, turn_count)
    context = chat_context(insight)
    state = AgentState(context["company_id"], context["news_id"])
    limits = (limits or AgentLimits()).model_copy(update={"analysis_tokens": CHAT_OUTPUT_TOKENS})

    started = perf_counter()

    def fallback(reason):
        logger.info("chat stage=ask reason=%s attempts=%s elapsed=%.3f usage=%s",
                    reason, state.llm_calls, perf_counter() - started, state.usage)
        return contextual_answer(context, question, dialogue, reason)

    # These are untrusted conversation hints, not primary evidence.
    result = insight["result"]
    from app.agent.recommendations import industry_terms
    terms = industry_terms(" ".join([question, *[row["question"] for row in dialogue]]))

    def relevant_texts(rows, maximum, size):
        texts = [row.get("text", "") if isinstance(row, dict) else str(row) for row in rows]
        texts.sort(key=lambda text: -len(terms & industry_terms(text)))
        return [text[:size] for text in texts[:maximum]]

    summary = {"ai_interpretation": str(result.get("ai_interpretation", ""))[:400],
               "suggested_actions": relevant_texts(result.get("suggested_actions", []), 3, 120),
               "risks": relevant_texts(result.get("risks", []), 2, 160),
               "opportunities": relevant_texts(result.get("opportunities", []), 2, 160),
               "warnings": relevant_texts(result.get("warnings", []), 3, 120)}

    async def respond():
        if tools.llm is None:
            return fallback("unavailable")
        if not context["evidence"]:
            return fallback("no_evidence")
        error = None
        for attempt in range(2):
            try:
                draft = await complete_json(tools, state, limits, "ask", {
                    "context": context, "question": question, "history": dialogue,
                    "analysis_summary": summary,
                    "instruction": "Дай короткий деловой ответ и конкретные следующие действия. История нужна для понимания вопроса, "
                                   "и analysis_summary не являются доказательствами. Определи тему по question, пользовательским условиям "
                                   "текущего цикла и новости. Финансирование обсуждай только по теме запроса. "
                                   "Учитывай только явно сообщённую роль. При нехватке данных назови пробел, предложи "
                                   "конкретную помощь и один-два предметных вопроса; иначе отвечай прямо. "
                                   "Не выдумывай условия банка и юридические обязанности. Пользовательские числа — только в user_context.",
                    "verification_error": error,
                }, ChatDraft)
                answer = verified_answer(draft, context, question, previous_questions=[row["question"] for row in dialogue])
                logger.info("chat stage=ask reason=verified attempt=%s elapsed=%.3f usage=%s",
                            attempt + 1, perf_counter() - started, state.usage)
                return answer
            except AgentUnavailable:
                return fallback("unavailable")
            except EvidenceError as exc:
                error = str(exc) + ". Исправь без добавления фактов."
                if "числ" in str(exc).lower():
                    error += " Дай качественный ответ без количественных утверждений, цифровых кодов и нумерованных пунктов."
                logger.info("chat stage=ask reason=rejected attempt=%s elapsed=%.3f", attempt + 1, perf_counter() - started)
        return fallback("rejected")

    try:
        answer = await asyncio.wait_for(respond(), limits.deadline_seconds)
    except TimeoutError:
        answer = fallback("timeout")
    if any(item.get("chat_excerpt_limited") for item in context["evidence"].values()):
        answer.warnings.append("Для чата выбраны ограниченные выдержки; полный текст источников не передавался.")
    if context["news"].get("body_is_summary"):
        answer.warnings.append("Доступно краткое описание новости; полный текст не проверялся.")
    if any(term in question.lower() for term in ("сейчас", "сегодня", "изменилось", "актуаль")):
        available_date = context.get("as_of") or context["news"].get("published_at")
        answer.warnings.append(f"Доступны сохранённые сведения на дату {available_date or 'неизвестна'}; последующие изменения не проверялись.")
    if getattr(tools.llm, "offline", False):
        answer.warnings.append("Офлайн-демонстрация: ответ составлен без языковой модели.")
    if count and count % CHAT_CYCLE == 0:
        answer.warnings.append("Контекст диалога обновлён после трёх вопросов; компания и новость сохранены.")
    return answer.model_dump(mode="json")
