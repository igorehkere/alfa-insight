from app.connect import engine
from app.models import News


def main():
    News.__table__.create(
        bind=engine,
        checkfirst=True,
    )
    print("Таблица news_items создана")


if __name__ == "__main__":
    main()