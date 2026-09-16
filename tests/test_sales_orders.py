"""Integration tests for Step 4: Sales order demand and confirmation."""
import os
from decimal import Decimal
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from app.db import get_session
from app.main import app
from tests.db_support import connect, in_schema, migrate_schema

pytestmark = pytest.mark.skipif(
    not os.environ.get("DB_HOST"), reason="Use the Docker Compose test service.",
)


@pytest.fixture(scope="module")
def sales_schema():
    name = "test_sales_" + uuid4().hex
    with connect() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        migrate_schema(name)
        with in_schema(name) as connection:
            connection.execute("""
                INSERT INTO organizations(id,code,name) VALUES (1,'A','A'),(2,'B','B');
                INSERT INTO business_partners(id,organization_id,code,name)
                VALUES (1,1,'CUST-1','Primary Customer'),
                       (2,1,'CUST-INACTIVE','Inactive Customer'),
                       (3,2,'CUST-2','Other Org Customer');
                UPDATE business_partners SET is_active=false WHERE id=2;

                INSERT INTO items(id,organization_id,sku,name,item_type,base_uom)
                VALUES (1,1,'PV-550','550W Solar Panel','FINISHED_GOOD','EA'),
                       (2,1,'CELL-M10','M10 Solar Cell','COMPONENT','EA'),
                       (3,2,'OTHER','Other Item','FINISHED_GOOD','EA');

                INSERT INTO item_revisions(id,organization_id,item_id,revision_code,status)
                VALUES (1,1,1,'REV-A','ACTIVE'),
                       (2,1,1,'REV-DRAFT','DRAFT'),
                       (3,1,1,'REV-OBS','OBSOLETE'),
                       (4,1,2,'REV-A','ACTIVE'),
                       (5,2,3,'REV-A','ACTIVE');
            """)
        yield name
    finally:
        with connect() as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture(scope="module")
def sales_engine(sales_schema):
    engine = create_engine("postgresql+psycopg://", creator=lambda: in_schema(sales_schema))
    yield engine
    engine.dispose()


@pytest.fixture
def client(sales_engine):
    def test_session():
        with Session(sales_engine, expire_on_commit=False) as session, session.begin():
            yield session

    app.dependency_overrides[get_session] = test_session
    with TestClient(app, headers={"X-Organization-ID": "1"}) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_session, None)


def create_order(client, order_number="SO-101", customer_id=1, lines=None, currency="EUR"):
    payload = {
        "order_number": order_number,
        "customer_id": customer_id,
        "currency_code": currency,
        "lines": lines if lines is not None else [
            {"item_revision_id": 1, "quantity": "100", "unit_price": "250.00"}
        ],
    }
    return client.post("/sales-orders", json=payload)


def test_draft_order_can_be_confirmed(client):
    # Create draft order with lines
    resp = create_order(client, "SO-CONFIRM-1")
    assert resp.status_code == 201, resp.text
    order = resp.json()
    order_id = order["id"]
    assert order["status"] == "DRAFT"
    assert order["order_number"] == "SO-CONFIRM-1"
    assert len(order["lines"]) == 1
    assert Decimal(order["lines"][0]["quantity"]) == Decimal("100")
    assert order["lines"][0]["sku"] == "PV-550"

    # Confirm order via explicit business operation
    confirm_resp = client.post(f"/sales-orders/{order_id}/confirm")
    assert confirm_resp.status_code == 200, confirm_resp.text
    confirmed = confirm_resp.json()
    assert confirmed["status"] == "CONFIRMED"

    # Repeated confirmation is safe and idempotent
    repeat_resp = client.post(f"/sales-orders/{order_id}/confirm")
    assert repeat_resp.status_code == 200
    assert repeat_resp.json()["status"] == "CONFIRMED"


def test_empty_order_cannot_be_confirmed(client):
    # Create empty draft order
    resp = create_order(client, "SO-EMPTY-1", lines=[])
    assert resp.status_code == 201, resp.text
    order_id = resp.json()["id"]
    assert resp.json()["status"] == "DRAFT"
    assert resp.json()["lines"] == []

    # Attempt to confirm empty order
    confirm_resp = client.post(f"/sales-orders/{order_id}/confirm")
    assert confirm_resp.status_code == 422, confirm_resp.text
    assert confirm_resp.json()["detail"]["code"] == "empty_order"

    # Add a line to the draft order
    line_resp = client.post(f"/sales-orders/{order_id}/lines", json={
        "item_revision_id": 1,
        "quantity": "50",
        "unit_price": "240.00",
    })
    assert line_resp.status_code == 201, line_resp.text
    assert line_resp.json()["line_no"] == 1

    # Now confirmation succeeds
    confirm_resp = client.post(f"/sales-orders/{order_id}/confirm")
    assert confirm_resp.status_code == 200, confirm_resp.text
    assert confirm_resp.json()["status"] == "CONFIRMED"
    assert len(confirm_resp.json()["lines"]) == 1


def test_cancelled_order_cannot_be_confirmed(client):
    resp = create_order(client, "SO-CANCEL-1")
    assert resp.status_code == 201
    order_id = resp.json()["id"]

    # Explicit cancel
    cancel_resp = client.post(f"/sales-orders/{order_id}/cancel")
    assert cancel_resp.status_code == 200, cancel_resp.text
    assert cancel_resp.json()["status"] == "CANCELLED"

    # Attempt to confirm cancelled order must fail
    confirm_resp = client.post(f"/sales-orders/{order_id}/confirm")
    assert confirm_resp.status_code == 409, confirm_resp.text
    assert confirm_resp.json()["detail"]["code"] == "order_cancelled"


def test_invalid_revision_cannot_be_ordered(client):
    # Non-existent revision
    resp = create_order(client, "SO-INV-1", lines=[{"item_revision_id": 999999, "quantity": "10"}])
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "revision_not_found"

    # Revision belonging to another organization
    resp = create_order(client, "SO-INV-2", lines=[{"item_revision_id": 5, "quantity": "10"}])
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "revision_not_found"

    # DRAFT revision cannot be ordered
    resp = create_order(client, "SO-INV-3", lines=[{"item_revision_id": 2, "quantity": "10"}])
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_revision_status"

    # OBSOLETE revision cannot be ordered
    resp = create_order(client, "SO-INV-4", lines=[{"item_revision_id": 3, "quantity": "10"}])
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "invalid_revision_status"

    # Adding an invalid revision line to an existing draft order fails
    draft = create_order(client, "SO-INV-5", lines=[]).json()
    add_resp = client.post(f"/sales-orders/{draft['id']}/lines", json={
        "item_revision_id": 3,
        "quantity": "5",
    })
    assert add_resp.status_code == 422
    assert add_resp.json()["detail"]["code"] == "invalid_revision_status"


def test_order_number_remains_unique(client):
    # First creation succeeds
    resp1 = create_order(client, "SO-UNIQUE-001")
    assert resp1.status_code == 201

    # Second creation with identical order number in same organization fails
    resp2 = create_order(client, "SO-UNIQUE-001")
    assert resp2.status_code == 409, resp2.text
    assert resp2.json()["detail"]["code"] == "duplicate_order_number"

    # Same order number in a different organization succeeds
    client.headers["X-Organization-ID"] = "2"
    resp3 = create_order(client, "SO-UNIQUE-001", customer_id=3, lines=[{"item_revision_id": 5, "quantity": "10"}])
    assert resp3.status_code == 201


def test_patch_not_allowed_and_explicit_workflow(client):
    # Arbitrary PATCH is not supported
    resp = create_order(client, "SO-PATCH-1")
    assert resp.status_code == 201
    order_id = resp.json()["id"]

    patch_resp = client.patch(f"/sales-orders/{order_id}", json={"status": "CONFIRMED"})
    assert patch_resp.status_code == 405  # Method Not Allowed

    # Cannot add lines to a confirmed order
    client.post(f"/sales-orders/{order_id}/confirm")
    add_line_resp = client.post(f"/sales-orders/{order_id}/lines", json={
        "item_revision_id": 1,
        "quantity": "10",
    })
    assert add_line_resp.status_code == 409
    assert add_line_resp.json()["detail"]["code"] == "order_locked"


def test_inactive_customer_rejected(client):
    # Customer 2 is inactive
    resp = create_order(client, "SO-INACT-CUST", customer_id=2)
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "customer_inactive"
