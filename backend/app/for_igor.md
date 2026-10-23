# Игорю: как вызывать функции Димы

## Какие ручки что вызывают

| Ручка Игоря | Вызов функции Димы | Что делать с результатом |
|---|---|---|
| `GET /api/news?company_id=...&limit=10` | `await recommend_news(company_id, tools, limit=10)` | Вернуть `company_id + items` |
| `GET /api/news/{news_id}` | Агент не нужен | Вернуть новость из своего сервиса |
| `POST /api/insights/analyze` | `await run_analysis(company_id, news_id, tools)` | Сохранить анализ и контекст; добавить ID; вернуть публичный ответ |
| `POST /api/insights/{insight_id}/ask` | `await answer_question(saved_insight, question, tools)` | Загрузить сохранённый анализ с контекстом; вернуть ответ |
| `GET /api/insights/{insight_id}/trace` | Агент не нужен | Вернуть `{"items": saved_agent_trace}` |

## 1. Какие параметры у тебя уже есть

- `company_id` — ID выбранной пользователем компании.
- `news_id` — ID новости из твоей БД. В функцию передавай строку: `str(news.id)`.
- `limit=10` — сколько новостей вернуть.
- `question` — текст вопроса из запроса пользователя.

## 2. Что пока объявить заглушками

- `tools = ToolsStub()` — временная замена нашего объекта с инструментами.
- `saved_insight = SavedInsightStub().model_dump()` — временный пустой словарь вместо сохранённого анализа.

Они подходят **только для функций-заглушек ниже**. Настоящим функциям потребуются
настоящие инструменты и сохранённые данные. Позже Дима поможет подключить их.

## 3. Скопируй в `backend/app/dima_stub.py`

Это самостоятельный временный файл: наш остальной код не нужен.
Функции возвращают пустые ответы нужной формы, без БД, модели и расхода токенов.

```python
from pydantic import BaseModel, Field


class ToolsStub(BaseModel):
    pass


class SavedInsightStub(BaseModel):
    pass


class NewsFeedStub(BaseModel):
    company_id: str
    items: list[dict] = Field(default_factory=list)


class AnalysisStub(BaseModel):
    result: dict = Field(default_factory=dict)
    agent_trace: list[dict] = Field(default_factory=list)


class AnalysisRunStub(BaseModel):
    analysis: AnalysisStub = Field(default_factory=AnalysisStub)
    context: dict = Field(default_factory=dict)
    usage: list[dict] = Field(default_factory=list)


class AnswerStub(BaseModel):
    answer: str = ""
    source_ids: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)


async def recommend_news(
    company_id: str, tools: ToolsStub, limit: int = 10,
) -> dict:
    return NewsFeedStub(company_id=company_id).model_dump()


async def run_analysis(
    company_id: str, news_id: str, tools: ToolsStub,
) -> AnalysisRunStub:
    return AnalysisRunStub()


async def answer_question(
    saved_insight: dict, question: str, tools: ToolsStub,
) -> dict:
    return AnswerStub().model_dump()
```

## 4. Простейший эндпоинт

Добавь в свой существующий `backend/app/main.py`. Объект `app` у тебя уже есть —
создавать ещё один `FastAPI()` не нужно. Если ручка уже появилась, используй этот
вызов внутри неё, не объявляй тот же маршрут повторно.

```python
from fastapi import Query
from app.dima_stub import ToolsStub, NewsFeedStub, recommend_news


@app.get("/api/news", response_model=NewsFeedStub)
async def news_feed(
    company_id: str = Query(min_length=1),
    limit: int = Query(default=10, ge=1, le=100),
) -> NewsFeedStub:
    tools = ToolsStub()
    data = await recommend_news(company_id, tools, limit=limit)
    return NewsFeedStub.model_validate(data)
```

Запрос `GET /api/news?company_id=U_25835&limit=10` пока вернёт:

```json
{"company_id": "U_25835", "items": []}
```

Это пустая лента-заглушка, а не результат настоящего подбора.

## 5. Как вызвать остальные функции

Эти строки размещаются **внутри твоих `async`-обработчиков**. `company_id`,
`news_id` и `question` берутся из соответствующего запроса.

```python
from app.dima_stub import (
    ToolsStub, SavedInsightStub, run_analysis, answer_question,
)

# В обработчике анализа:
tools = ToolsStub()
run = await run_analysis(company_id, news_id, tools)
# run.analysis — аналитика и журнал; run.context — контекст; run.usage — учёт модели.

# В обработчике вопроса, пока нет сохранённого настоящего анализа:
tools = ToolsStub()
saved_insight = SavedInsightStub().model_dump()
answer = await answer_question(saved_insight, question, tools)
# answer — словарь с answer, source_ids, warnings. Его возвращаешь из ручки.
```

Когда подключим настоящие функции: сохраняй `run.analysis` вместе с `run.context`
и ID компании/новости. Для вопроса загружай эти данные из БД вместо пустого
`SavedInsightStub`. В настоящем `run` поле `analysis` будет обычным словарём;
здесь `AnalysisStub` лишь показывает его структуру. `context` не отдавай frontend.
