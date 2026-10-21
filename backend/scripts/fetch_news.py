import argparse
import logging

from app.connect import SessionLocal, engine
from app.models import News
from app.news.parser import RSS_SOURCES, fetch_rss
from app.news.service import save_news


def main() -> int:
    parser = argparse.ArgumentParser(description="Импорт новостей из RSS в PostgreSQL")
    parser.add_argument(
        "--source", choices=["all", *RSS_SOURCES], default="all",
        help="RSS-лента: news, press или all",
    )
    args = parser.parse_args()

    # Только news_items
    News.__table__.create(bind=engine, checkfirst=True)

    sources = RSS_SOURCES.items() if args.source == "all" else [
        (args.source, RSS_SOURCES[args.source])
    ]

    success = 0
    for source_key, (source_name, url) in sources:
        try:
            items = fetch_rss(url, source_name)
            if not items:
                logging.warning("%s: новостей не найдено", source_key)
                continue
            with SessionLocal.begin() as session:
                count = save_news(session, items)
            success += 1
            print(f"{source_key}: получено {len(items)}, добавлено {count}, повторных {len(items) - count}")
        except Exception:
            logging.exception("Ошибка получения или сохранения RSS (%s)", source_key)

    if success == 0:
        print("Нет успешно обработанных RSS-источников")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())