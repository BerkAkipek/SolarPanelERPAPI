"""Database connection configuration shared by the API and migrations."""
import os

from sqlalchemy.engine import URL


def database_url() -> URL:
    required = ("DB_USER", "DB_PASSWORD", "DB_NAME")
    missing = [name for name in required if not os.environ.get(name)]
    if missing:
        raise RuntimeError("Missing database configuration: " + ", ".join(missing))
    try:
        port = int(os.environ.get("DB_PORT", "5432"))
        if not 1 <= port <= 65535:
            raise ValueError
    except ValueError as error:
        raise RuntimeError("DB_PORT must be an integer between 1 and 65535") from error
    return URL.create(
        "postgresql+psycopg",
        username=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ.get("DB_HOST", "localhost"),
        port=port,
        database=os.environ["DB_NAME"],
    )
