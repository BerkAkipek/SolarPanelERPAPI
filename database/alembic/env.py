import logging

from alembic import context
from sqlalchemy import create_engine
from sqlalchemy.pool import NullPool

from app.config import database_url

logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")


def run_migrations(connection):
    context.configure(connection=connection)
    with context.begin_transaction():
        context.run_migrations()


connection = context.config.attributes.get("connection")
if connection is not None:
    # Tests run the real migration chain inside a disposable schema.
    run_migrations(connection)
elif context.is_offline_mode():
    context.configure(url=database_url(), literal_binds=True)
    with context.begin_transaction():
        context.run_migrations()
else:
    engine = create_engine(database_url(), poolclass=NullPool, hide_parameters=True)
    try:
        with engine.connect() as connection:
            run_migrations(connection)
    finally:
        engine.dispose()
