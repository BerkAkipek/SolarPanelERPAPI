"""One transaction per request, completed before an HTTP response is sent."""
from functools import lru_cache

from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.config import database_url


@lru_cache
def get_engine():
    return create_engine(
        database_url(), pool_pre_ping=True, hide_parameters=True,
        connect_args={"connect_timeout": 5},
    )


def get_session():
    with Session(get_engine(), expire_on_commit=False) as session, session.begin():
        yield session
