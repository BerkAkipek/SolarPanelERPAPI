"""Integration tests for Step 4: Sales order demand and confirmation."""
import os
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from threading import Barrier
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql
import pytest
from sqlalchemy import create_engine, text
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
    try:
        with TestClient(app, headers={"X-Organization-ID": "1"}) as test_client:
            yield test_client
    finally:
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


def test_create_get_and_append_persist_exact_values(client, sales_engine):
    response = client.post("/sales-orders", json={
        "order_number": "SO-PERSIST", "customer_id": 1, "currency_code": "EUR",
        "ordered_at": "2026-09-17T15:00:00+03:00",
        "required_at": "2026-09-20T15:00:00+03:00",
        "lines": [
            {"line_no": 2, "item_revision_id": 1, "quantity": "1.123456", "unit_price": "250.1234"},
            {"item_revision_id": 4, "quantity": "2", "unit_price": "0"},
            {"item_revision_id": 1, "quantity": "3"},
        ],
    })
    assert response.status_code == 201, response.text
    order = response.json()
    assert [line["line_no"] for line in order["lines"]] == [1, 2, 3]
    assert order["status"] == "DRAFT"
    assert order["ordered_at"] == "2026-09-17T12:00:00Z"
    assert order["required_at"] == "2026-09-20T12:00:00Z"
    added = client.post(f"/sales-orders/{order['id']}/lines", json={
        "item_revision_id": 4, "quantity": "0.000001", "unit_price": "99999999999999.9999",
    })
    assert added.status_code == 201, added.text
    assert added.json()["line_no"] == 4
    fetched = client.get(f"/sales-orders/{order['id']}")
    assert fetched.status_code == 200
    assert len(fetched.json()["lines"]) == 4
    with sales_engine.connect() as connection:
        header = connection.execute(text("""
            SELECT organization_id, customer_id, order_number, currency_code, status
            FROM sales_orders WHERE id=:id
        """), {"id": order["id"]}).one()
        assert tuple(header) == (1, 1, "SO-PERSIST", "EUR", "DRAFT")
        rows = connection.execute(text("""
            SELECT line_no, item_revision_id, quantity, unit_price FROM sales_order_lines
            WHERE sales_order_id=:id ORDER BY line_no
        """), {"id": order["id"]}).all()
    assert rows == [
        (1, 4, Decimal("2"), Decimal("0")),
        (2, 1, Decimal("1.123456"), Decimal("250.1234")),
        (3, 1, Decimal("3"), None),
        (4, 4, Decimal("0.000001"), Decimal("99999999999999.9999")),
    ]
    for actual, stored in zip(fetched.json()["lines"], rows):
        assert actual["sales_order_id"] == order["id"]
        assert actual["organization_id"] == 1
        assert Decimal(actual["quantity"]) == stored.quantity
        assert (Decimal(actual["unit_price"]) if actual["unit_price"] is not None else None) == stored.unit_price


def test_duplicate_lines_roll_back_whole_order_and_allow_corrected_retry(client, sales_engine):
    lines = [{"line_no": 1, "item_revision_id": 1, "quantity": "10"}] * 2
    response = create_order(client, "SO-ROLLBACK", lines=lines)
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "duplicate_line_no"
    with sales_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM sales_orders WHERE order_number='SO-ROLLBACK'")) == 0
        assert connection.scalar(text("""
            SELECT count(*) FROM sales_order_lines l LEFT JOIN sales_orders o ON o.id=l.sales_order_id
            WHERE o.id IS NULL
        """)) == 0
    assert create_order(client, "SO-ROLLBACK", lines=lines[:1]).status_code == 201


@pytest.mark.parametrize("field,value", [
    ("line_no", 0), ("line_no", -1), ("line_no", True), ("line_no", "1"),
    ("line_no", 2147483648), ("unit_price", "100000000000000"),
    ("unit_price", "0.00001"), ("unit_price", "-1"), ("unit_price", "NaN"),
    ("quantity", "0"), ("quantity", "0.0000001"), ("quantity", "Infinity"),
])
def test_invalid_line_values_rejected_on_create_and_append(client, field, value):
    number = "SO-VALIDATION-" + uuid4().hex
    line = {"item_revision_id": 1, "quantity": "1", field: value}
    assert create_order(client, number, lines=[line]).status_code == 422
    order = create_order(client, number, lines=[]).json()
    assert client.post(f"/sales-orders/{order['id']}/lines", json=line).status_code == 422
    assert client.get(f"/sales-orders/{order['id']}").json()["lines"] == []


def test_append_duplicate_and_number_exhaustion_preserve_existing_lines(client):
    order = create_order(client, "SO-MAX-LINE", lines=[
        {"line_no": 2147483647, "item_revision_id": 1, "quantity": "1"},
    ]).json()
    path = f"/sales-orders/{order['id']}"
    response = client.post(path + "/lines", json={"item_revision_id": 1, "quantity": "1"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "line_number_exhausted"
    response = client.post(path + "/lines", json={
        "line_no": 2147483647, "item_revision_id": 1, "quantity": "1",
    })
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "duplicate_line_no"
    assert len(client.get(path).json()["lines"]) == 1
    assert client.post(path + "/lines", json={
        "line_no": 1, "item_revision_id": 1, "quantity": "1",
    }).status_code == 201


def test_sales_order_reads_and_appends_are_tenant_scoped(client):
    order = create_order(client, "SO-SCOPE").json()
    for order_id, headers in [(order["id"], {"X-Organization-ID": "2"}), (999999, {})]:
        path = f"/sales-orders/{order_id}"
        responses = [client.get(path, headers=headers), client.post(path + "/lines", headers=headers,
            json={"item_revision_id": 5, "quantity": "1"})]
        for response in responses:
            assert response.status_code == 404
            assert response.json()["detail"]["code"] == "order_not_found"
    for customer_id in [3, 999999]:
        response = create_order(client, "SO-BAD-CUSTOMER", customer_id=customer_id)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "customer_not_found"
    assert len(client.get(f"/sales-orders/{order['id']}").json()["lines"]) == 1
    client.headers.pop("X-Organization-ID")
    assert client.get(f"/sales-orders/{order['id']}").status_code == 422
    assert create_order(client, "SO-NO-TENANT").status_code == 422


def test_concurrent_line_appends_persist_distinct_numbers(client):
    order = create_order(client, "SO-CONCURRENT-LINES", lines=[]).json()
    path = f"/sales-orders/{order['id']}"
    def append_line(_):
        return client.post(path + "/lines", json={"item_revision_id": 1, "quantity": "1"})
    with ThreadPoolExecutor(max_workers=4) as pool:
        responses = list(pool.map(append_line, range(4)))
    assert all(response.status_code == 201 for response in responses), [r.text for r in responses]
    lines = client.get(path).json()["lines"]
    assert [line["line_no"] for line in lines] == [1, 2, 3, 4]
    assert len({line["id"] for line in lines}) == 4


@pytest.mark.parametrize("status", ["DRAFT", "CONFIRMED", "RESERVED", "PARTIALLY_SHIPPED", "SHIPPED", "CANCELLED"])
@pytest.mark.parametrize("operation", ["confirm", "cancel"])
def test_transition_matrix(client, sales_engine, status, operation):
    order = create_order(client, "SO-MATRIX-" + uuid4().hex).json()
    path = f"/sales-orders/{order['id']}"
    with sales_engine.begin() as connection:
        connection.execute(text("UPDATE sales_orders SET status=:status WHERE id=:id"),
                           {"status": status, "id": order["id"]})
    before = client.get(path).json()
    allowed = status in ({"DRAFT", "CONFIRMED"} if operation == "confirm" else {"DRAFT", "CONFIRMED", "CANCELLED"})
    response = client.post(path + "/" + operation)
    assert response.status_code == (200 if allowed else 409), response.text
    if allowed:
        target = "CONFIRMED" if operation == "confirm" else "CANCELLED"
        assert response.json()["status"] == target
        assert response.json()["lines"] == before["lines"]
        assert client.post(path + "/" + operation).json() == response.json()
    else:
        expected = "order_invalid_status"
        if operation == "confirm" and status == "CANCELLED":
            expected = "order_cancelled"
        if operation == "cancel" and status in {"SHIPPED", "PARTIALLY_SHIPPED"}:
            expected = "order_shipped"
        assert response.json()["detail"]["code"] == expected
        assert client.get(path).json() == before


@pytest.mark.parametrize("operation", ["confirm", "cancel"])
def test_transitions_require_tenant_and_existing_order(client, operation):
    created = create_order(client, "SO-SCOPE-" + uuid4().hex)
    assert created.status_code == 201, created.text
    order = created.json()
    for order_id, headers in [(order["id"], {"X-Organization-ID": "2"}), (999999, {})]:
        response = client.post(f"/sales-orders/{order_id}/{operation}", headers=headers)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "order_not_found"
    assert client.get(f"/sales-orders/{order['id']}").json()["status"] == "DRAFT"
    client.headers.pop("X-Organization-ID")
    assert client.post(f"/sales-orders/{order['id']}/{operation}").status_code == 422


def stock_for_order(client, sales_engine):
    order = create_order(client, "SO-ALLOCATION-" + uuid4().hex).json()
    with sales_engine.begin() as connection:
        location_id = connection.scalar(text("""
            INSERT INTO inventory_locations(organization_id,code,name,location_type)
            VALUES (1,:code,'Transition test','WAREHOUSE') RETURNING id
        """), {"code": uuid4().hex})
    response = client.post("/inventory/movements", json={
        "movement_type": "RECEIPT",
        "lines": [{"item_revision_id": 1, "to_location_id": location_id, "quantity": "100"}],
    })
    assert response.status_code == 201
    payload = {"item_revision_id": 1, "location_id": location_id, "quantity": "100",
               "sales_order_line_id": order["lines"][0]["id"]}
    return order, payload


@pytest.mark.parametrize("reservation_status", ["ACTIVE", "RELEASED", "CONSUMED"])
def test_cancellation_respects_reservation_history(client, sales_engine, reservation_status):
    order, payload = stock_for_order(client, sales_engine)
    path = f"/sales-orders/{order['id']}"
    assert client.post(path + "/confirm").status_code == 200
    response = client.post("/inventory/reservations", json=payload)
    assert response.status_code == 201
    reservation_id = response.json()["id"]
    if reservation_status != "ACTIVE":
        operation = "release" if reservation_status == "RELEASED" else "consume"
        assert client.post(f"/inventory/reservations/{reservation_id}/{operation}").status_code == 200
    response = client.post(path + "/cancel")
    if reservation_status == "RELEASED":
        assert response.status_code == 200
        assert response.json()["status"] == "CANCELLED"
        retry_allocation = client.post("/inventory/reservations", json=payload)
        assert retry_allocation.status_code == 409
        assert retry_allocation.json()["detail"]["code"] == "order_cancelled"
    else:
        assert response.status_code == 409
        assert response.json()["detail"]["code"] == "order_has_reservations"
        assert client.get(path).json()["status"] == "CONFIRMED"
    assert client.get(f"/inventory/reservations/{reservation_id}").json()["status"] == reservation_status


def test_concurrent_cancellation_and_reservation_cannot_both_succeed(client, sales_engine):
    order, payload = stock_for_order(client, sales_engine)
    barrier = Barrier(2)
    def cancel():
        barrier.wait(timeout=5)
        return client.post(f"/sales-orders/{order['id']}/cancel")
    def reserve():
        barrier.wait(timeout=5)
        return client.post("/inventory/reservations", json=payload)
    with ThreadPoolExecutor(max_workers=2) as pool:
        cancel_future = pool.submit(cancel)
        reserve_future = pool.submit(reserve)
        cancelled, reserved = cancel_future.result(timeout=10), reserve_future.result(timeout=10)
    if cancelled.status_code == 200:
        assert reserved.status_code == 409
        assert reserved.json()["detail"]["code"] == "order_cancelled"
    else:
        assert cancelled.status_code == 409
        assert cancelled.json()["detail"]["code"] == "order_has_reservations"
        assert reserved.status_code == 201


@pytest.mark.parametrize("change,code", [("revision", "invalid_revision_status"), ("item", "item_inactive")])
def test_confirmation_revalidates_catalog_and_preserves_draft(client, sales_engine, change, code):
    order = create_order(client, "SO-REVALIDATE-" + uuid4().hex).json()
    statement = "UPDATE item_revisions SET status='OBSOLETE' WHERE id=1" if change == "revision" else "UPDATE items SET is_active=false WHERE id=1"
    restore = "UPDATE item_revisions SET status='ACTIVE' WHERE id=1" if change == "revision" else "UPDATE items SET is_active=true WHERE id=1"
    try:
        with sales_engine.begin() as connection:
            connection.execute(text(statement))
        response = client.post(f"/sales-orders/{order['id']}/confirm")
        assert response.status_code == 422
        assert response.json()["detail"]["code"] == code
        assert client.get(f"/sales-orders/{order['id']}").json()["status"] == "DRAFT"
    finally:
        with sales_engine.begin() as connection:
            connection.execute(text(restore))
