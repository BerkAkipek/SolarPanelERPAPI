"""Real PostgreSQL connections and migrations shared by integration tests."""
from pathlib import Path

import psycopg
from psycopg import sql
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from app.config import database_url


def connect():
    return psycopg.connect(**database_url().translate_connect_args(username="user", database="dbname"))


def in_schema(name):
    connection = connect()
    connection.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(name)))
    connection.commit()
    return connection


def migrate_schema(name, revision="head"):
    engine = create_engine("postgresql+psycopg://", creator=connect, poolclass=NullPool)
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(
                sql.SQL("SET search_path TO {}").format(sql.Identifier(name)).as_string()
            )
            config = Config(str(Path(__file__).parents[1] / "alembic.ini"))
            config.attributes["connection"] = connection
            command.upgrade(config, revision)
    finally:
        engine.dispose()
