"""HTTP transaction fixtures shared by product and inventory integration tests."""
from fastapi.testclient import TestClient
import pytest
from sqlalchemy.orm import Session

from app.db import get_session
from app.main import app


@pytest.fixture
def api_connection(api_engine):
    with api_engine.connect() as connection, connection.begin():
        yield connection
        connection.rollback()


@pytest.fixture
def api(api_connection):
    # A request owns one savepoint; the enclosing test transaction is rolled back.
    def test_session():
        with Session(
            bind=api_connection, join_transaction_mode="create_savepoint", expire_on_commit=False,
        ) as session, session.begin():
            yield session

    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app, headers={"X-Organization-ID": "1"}) as client:
            yield client
    finally:
        app.dependency_overrides.pop(get_session, None)
