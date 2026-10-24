"""Local JSON harness, not a replacement for Igor's HTTP/persistence layer."""
from __future__ import annotations

import asyncio
import hashlib
import json
import re
from pathlib import Path

from app.agent.errors import InvalidInput, NotFound
from app.agent.prompts import PROMPT_VERSION
from app.agent.schema import InsightResponse
from app.agent.service import answer_question, compact_profile, run_analysis
from app.rag.ingest import atomic_json


class JsonApplication:
    """One local process. Production needs shared DB cache and atomic claims."""
    def __init__(self, tools, directory: Path):
        if not getattr(tools.llm, "offline", False):
            raise InvalidInput("JSON-приложение предназначено для офлайн-проверки; настоящий клиент подключается через сервисный слой")
        self.tools, self.directory = tools, Path(directory)
        self._locks: dict[str, asyncio.Lock] = {}

    def _path(self, insight_id: str):
        if not re.fullmatch(r"insight-[a-f0-9]{64}", insight_id):
            raise InvalidInput("Неверный insight_id")
        return self.directory / (insight_id + ".json")

    def load(self, insight_id):
        path = self._path(insight_id)
        if not path.is_file():
            raise NotFound("Анализ не найден")
        envelope = json.loads(path.read_text(encoding="utf-8"))
        if envelope["public"]["company_id"] != self.tools.company_id:
            raise InvalidInput("Анализ принадлежит другой компании")
        return envelope

    async def analyze(self, company_id: str, news_id: str, *, force_refresh: bool = False):
        profile = compact_profile(await self.tools.get_company_profile(company_id))
        news = await self.tools.get_news(news_id)
        material = {"profile": profile, "news": news, "corpus": self.tools.corpus_version,
                    "prompt": PROMPT_VERSION, "model": self.tools.llm.cache_identity}
        digest = hashlib.sha256(json.dumps(material, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
        insight_id = "insight-" + digest
        async with self._locks.setdefault(insight_id, asyncio.Lock()):
            path = self._path(insight_id)
            if path.exists() and not force_refresh:
                return self.load(insight_id)["public"]
            run = await run_analysis(company_id, news_id, self.tools)
            public = InsightResponse(insight_id=insight_id, company_id=company_id,
                                     news_id=news_id, **run.analysis).model_dump(mode="json")
            old = self.load(insight_id) if path.exists() else None
            history = (old.get("history", []) + [{"public": old["public"], "context": old["context"],
                                                  "chat": old.get("chat", [])}]) if old else []
            atomic_json(path, {"public": public, "context": run.context, "usage": run.usage,
                               "chat": [], "history": history})
            return public

    async def ask(self, insight_id: str, question: str):
        async with self._locks.setdefault(insight_id, asyncio.Lock()):
            envelope = self.load(insight_id)
            response = await answer_question({**envelope["public"], "context": envelope["context"]}, question, self.tools,
                                             history=envelope["chat"])
            envelope["chat"].append({"question": question, "response": response})
            atomic_json(self._path(insight_id), envelope)
            return response
