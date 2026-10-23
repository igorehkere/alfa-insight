"""Safe offline demo: no .env loading, no LLMClient construction, no network."""
import argparse
import asyncio
import json
import tempfile
from pathlib import Path

from app.agent.local import JsonApplication
from app.agent.offline import OfflineLLM
from app.agent.recommendations import recommend_news
from app.agent.tools import JsonTools


async def demo(company_id: str, news_id: str, directory: Path):
    llm = OfflineLLM()
    tools = JsonTools(llm, company_id=company_id)
    application = JsonApplication(tools, directory)
    feed = await recommend_news(company_id, tools)
    insight = await application.analyze(company_id, news_id)
    calls = len(llm.calls)
    await application.analyze(company_id, news_id)
    reused = len(llm.calls) == calls
    answer = await application.ask(insight["insight_id"], "Каких данных не хватает для оценки?")
    return {"mode": "offline_demo_no_network", "recommendations": feed, "analysis": insight,
            "cache_reused": reused, "answer": answer}


def main():
    parser = argparse.ArgumentParser(description="Офлайн-проверка нашей части. Токены не используются.")
    parser.add_argument("--company-id", default="DEMO_AUTO")
    parser.add_argument("--news-id", default="demo-rates")
    parser.add_argument("--state-dir", type=Path, help="Сохранять JSON здесь; по умолчанию временный каталог")
    args = parser.parse_args()
    if args.state_dir:
        result = asyncio.run(demo(args.company_id, args.news_id, args.state_dir))
    else:
        with tempfile.TemporaryDirectory(prefix="alfa-agent-") as directory:
            result = asyncio.run(demo(args.company_id, args.news_id, Path(directory)))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
