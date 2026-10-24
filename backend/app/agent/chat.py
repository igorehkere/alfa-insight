"""Short chat with attributed user premises, bounded memory and an honest fallback."""
import asyncio
import copy

from pydantic import Field, ValidationError

from app.agent.errors import AgentUnavailable, EvidenceError, InvalidInput
from app.agent.schema import AnswerResponse, AskRequest, NonEmptyText
from app.agent.state import AgentLimits, AgentState
from app.agent.verifier import normalized, verify_answer

CHAT_CYCLE = 3
CHAT_OUTPUT_TOKENS = 650


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
    if draft.user_context:
        if not any(normalized(draft.user_context) in normalized(q) for q in (question, *previous_questions)):
            raise EvidenceError("user_context должен быть дословной цитатой пользовательского вопроса без новых данных")
        answer.answer = f"По вашим словам: «{draft.user_context}»\n\n{answer.answer}"
        answer.warnings.append("Условия из вашего вопроса сообщены вами и не подтверждены новостями или документами банка.")
    return answer


def general_answer(question, *, unavailable=False):
    text = question.lower()
    if any(term in text for term in ("станк", "оборудован", "кредит", "лизинг", "финансирован")):
        advice = ("Сравните кредит, лизинг и поэтапную закупку. Запросите предложения рынка, "
                  "сроки поставки и полную стоимость финансирования. Сопоставьте платежи с денежным потоком; "
                  "решение о срочном расширении обсудите с финансовым специалистом.")
    else:
        advice = ("Изучите предложения рынка и запросите условия у потенциальных партнёров. "
                  "Сравните стоимость, сроки и риски вариантов, оцените влияние на денежный поток. "
                  "Начните с ограниченного пилота и уточните недостающие данные у профильного специалиста.")
    answer = AnswerResponse(answer="Данных для адресного вывода недостаточно. " + advice, source_ids=[],
                            warnings=["Общие следующие действия; это не позиция банка и не подтверждённый вывод по компании."])
    if unavailable:
        answer.warnings.append("LLM недоступна или её ответ не прошёл проверку; показан резервный ответ без генерации.")
    return answer


def chat_context(insight):
    saved = insight["context"]
    ids = {source["id"] for source in insight["result"]["sources"]}
    # Prior assistant replies never become evidence. Bound the source text too.
    rows = [(sid, item) for sid, item in saved.get("evidence", {}).items() if sid in ids]
    rows.sort(key=lambda row: (row[0] != saved["news_id"], row[1].get("source", {}).get("type") != "bank", row[0]))
    evidence = {}
    for sid, item in rows[:3]:
        evidence[sid] = copy.deepcopy(item)
        evidence[sid]["text"] = item["text"][:1800]
    profile = saved.get("profile", {})
    return {"company_id": saved["company_id"], "news_id": saved["news_id"], "as_of": saved.get("as_of"),
            "profile": {key: copy.deepcopy(profile[key]) for key in ("company_id", "industry", "industry_code", "region", "features") if key in profile},
            "news": {key: saved.get("news", {}).get(key) for key in ("id", "title", "published_at", "provenance")},
            "evidence": evidence}


async def answer_question(insight, question, tools, *, history=None, turn_count=None, limits=None):
    from app.agent.service import complete_json
    try:
        question = AskRequest(question=question).question
    except ValidationError:
        raise InvalidInput("Вопрос не должен быть пустым") from None
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

    async def respond():
        if tools.llm is None:
            return general_answer(question, unavailable=True)
        if not context["evidence"]:
            return general_answer(question)
        error = None
        for attempt in range(2):
            try:
                draft = await complete_json(tools, state, limits, "ask", {
                    "context": context, "question": question, "history": dialogue,
                    "analysis_summary": str(insight["result"].get("ai_interpretation", ""))[:400],
                    "instruction": "Дай короткий деловой ответ и конкретные следующие действия. История нужна для понимания вопроса, "
                                   "но не является доказательством. При отсутствии прямой связи предложи самостоятельное изучение рынка "
                                   "и сравнение вариантов. Не выдумывай условия банка. Пользовательские числа — только в user_context.",
                    "verification_error": error,
                }, ChatDraft)
                return verified_answer(draft, context, question, previous_questions=[row["question"] for row in dialogue])
            except AgentUnavailable:
                return general_answer(question, unavailable=True)
            except EvidenceError as exc:
                error = str(exc) + ". Исправь: не добавляй данные; пользовательские числа только в дословной цитате user_context."
        return general_answer(question, unavailable=True)

    try:
        answer = await asyncio.wait_for(respond(), limits.deadline_seconds)
    except TimeoutError:
        answer = general_answer(question, unavailable=True)
    if getattr(tools.llm, "offline", False):
        answer.warnings.append("Офлайн-демонстрация: ответ составлен без языковой модели.")
    if count and count % CHAT_CYCLE == 0:
        answer.warnings.append("Контекст диалога обновлён после трёх вопросов; компания и новость сохранены.")
    return answer.model_dump(mode="json")
