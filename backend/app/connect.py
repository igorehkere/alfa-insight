import os
from collections.abc import Iterator
from pathlib import Path

import psycopg
from psycopg.rows import dict_row
from dotenv import load_dotenv

load_dotenv(Path(__file__).resolve().parents[2] / ".env")
DEFAULT_DSN = os.getenv("DATABASE_URL")


def connect() -> Iterator[psycopg.Connection]:
    with psycopg.connect(
        os.environ.get("DATABASE_URL", DEFAULT_DSN),
        row_factory=dict_row,
    ) as conn:
        yield conn