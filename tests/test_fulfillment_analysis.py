"""Integration tests for Step 5: Fulfillment analysis."""
import os
from decimal import Decimal
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


@pytest.fixture
def fulfillment_schema():
    name = "test_fulf_" + uuid4().hex
    with connect() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        migrate_schema(name)
        with in_schema(name) as connection:
            connection.execute("""
                INSERT INTO organizations(id,code,name) VALUES (1,'A','A'),(2,'B','B');
                INSERT INTO business_partners(id,organization_id,code,name)
                VALUES (1,1,'CUST-1','Solar EPC Partner');

                INSERT INTO items(id,organization_id,sku,name,item_type,base_uom)
                VALUES (1,1,'PV-550','550W Solar Panel','FINISHED_GOOD','EA'),
                       (2,1,'PV-400','400W Solar Panel','FINISHED_GOOD','EA');

                INSERT INTO item_revisions(id,organization_id,item_id,revision_code,status)
                VALUES (1,1,1,'REV-A','ACTIVE'),
                       (2,1,2,'REV-A','ACTIVE');

                INSERT INTO inventory_locations(id,organization_id,code,name,location_type)
                VALUES (1,1,'RAW-WH','Main Warehouse','WAREHOUSE'),
                       (2,1,'QA-HOLD','Quarantine Area','QUARANTINE');
            """)
        yield name
    finally:
        with connect() as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def fulfillment_engine(fulfillment_schema):
    engine = create_engine("postgresql+psycopg://", creator=lambda: in_schema(fulfillment_schema))
    yield engine
    engine.dispose()


@pytest.fixture
def client(fulfillment_engine):
    def test_session():
        with Session(fulfillment_engine, expire_on_commit=False) as session, session.begin():
            yield session

    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app, headers={"X-Organization-ID": "1"}) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_session, None)


def receive(client, quantity, revision=1, location=1):
    resp = client.post("/inventory/movements", json={
        "movement_type": "RECEIPT",
        "lines": [{"item_revision_id": revision, "to_location_id": location, "quantity": quantity}],
    })
    assert resp.status_code == 201, resp.text


def create_order(client, order_number, lines):
    resp = client.post("/sales-orders", json={
        "order_number": order_number,
        "customer_id": 1,
        "currency_code": "EUR",
        "lines": lines,
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_fulfillment_analysis_all_from_stock(client):
    # Stock 100 available
    receive(client, "100", revision=1)

    # Order 40
    order = create_order(client, "SO-ALL-STOCK-1", [{"item_revision_id": 1, "quantity": "40"}])
    order_id = order["id"]

    resp = client.get(f"/sales-orders/{order_id}/fulfillment-analysis")
    assert resp.status_code == 200, resp.text
    analysis = resp.json()

    assert analysis["sales_order_id"] == order_id
    assert analysis["can_fulfill_entirely_from_stock"] is True
    assert Decimal(analysis["total_ordered"]) == Decimal("40")
    assert Decimal(analysis["total_from_stock"]) == Decimal("40")
    assert Decimal(analysis["total_production_required"]) == Decimal("0")

    line = analysis["lines"][0]
    assert line["sku"] == "PV-550"
    assert Decimal(line["ordered"]) == Decimal("40")
    assert Decimal(line["available"]) == Decimal("100")
    assert Decimal(line["ship_from_stock"]) == Decimal("40")
    assert Decimal(line["production_required"]) == Decimal("0")


def test_fulfillment_analysis_partial_stock(client):
    # Receive exactly 30 of PV-400
    receive(client, "30", revision=2)

    order = create_order(client, "SO-PARTIAL-1", [{"item_revision_id": 2, "quantity": "100"}])
    order_id = order["id"]

    resp = client.get(f"/sales-orders/{order_id}/fulfillment-analysis")
    assert resp.status_code == 200, resp.text
    analysis = resp.json()

    assert analysis["can_fulfill_entirely_from_stock"] is False
    assert Decimal(analysis["total_ordered"]) == Decimal("100")
    assert Decimal(analysis["total_from_stock"]) == Decimal("30")
    assert Decimal(analysis["total_production_required"]) == Decimal("70")

    line = analysis["lines"][0]
    assert line["sku"] == "PV-400"
    assert Decimal(line["ordered"]) == Decimal("100")
    assert Decimal(line["available"]) == Decimal("30")
    assert Decimal(line["ship_from_stock"]) == Decimal("30")
    assert Decimal(line["production_required"]) == Decimal("70")


def test_fulfillment_analysis_zero_stock(client):
    # With no receipts, the full demand requires production.
    order2 = create_order(client, "SO-SHORTAGE-2", [{"item_revision_id": 2, "quantity": "200"}])

    resp = client.get(f"/sales-orders/{order2['id']}/fulfillment-analysis")
    assert resp.status_code == 200
    line = resp.json()["lines"][0]
    assert Decimal(line["ordered"]) == Decimal("200")
    assert Decimal(line["available"]) == Decimal("0")
    assert Decimal(line["ship_from_stock"]) == Decimal("0")
    assert Decimal(line["production_required"]) == Decimal("200")


def test_fulfillment_analysis_is_strictly_read_only(client, fulfillment_engine):
    order = create_order(client, "SO-READONLY-1", [{"item_revision_id": 1, "quantity": "50"}])
    order_id = order["id"]

    with fulfillment_engine.connect() as conn:
        prod_orders_before = conn.scalar(text("SELECT count(*) FROM production_orders"))
        reservations_before = conn.scalar(text("SELECT count(*) FROM stock_reservations"))
        movements_before = conn.scalar(text("SELECT count(*) FROM inventory_movements"))
        lines_before = conn.scalar(text("SELECT count(*) FROM inventory_movement_lines"))

    # PostgreSQL rejects any write, including updates that leave row counts unchanged.
    previous = app.dependency_overrides[get_session]
    def read_only_session():
        with Session(fulfillment_engine) as session, session.begin():
            session.execute(text("SET TRANSACTION READ ONLY"))
            yield session
    app.dependency_overrides[get_session] = read_only_session
    try:
        resp = client.get(f"/sales-orders/{order_id}/fulfillment-analysis")
        assert resp.status_code == 200, resp.text
        assert client.get(f"/sales-orders/{order_id}/fulfillment-analysis").json() == resp.json()
    finally:
        app.dependency_overrides[get_session] = previous

    # Verify no side-effects in the database
    with fulfillment_engine.connect() as conn:
        assert conn.scalar(text("SELECT count(*) FROM production_orders")) == prod_orders_before
        assert conn.scalar(text("SELECT count(*) FROM stock_reservations")) == reservations_before
        assert conn.scalar(text("SELECT count(*) FROM inventory_movements")) == movements_before
        assert conn.scalar(text("SELECT count(*) FROM inventory_movement_lines")) == lines_before

    # Verify sales order status remains unchanged
    order_check = client.get(f"/sales-orders/{order_id}").json()
    assert order_check["status"] == "DRAFT"


def test_fulfillment_analysis_ignores_quarantine_stock(client):
    receive(client, "100")
    # Put 200 units of PV-550 in QA-HOLD (location 2, QUARANTINE)
    receive(client, "200", revision=1, location=2)

    # Currently RAW-WH has 100 available. QA-HOLD has 200 in quarantine.
    # Total physical on-hand is 300, but reservable/available should only be 100.
    order = create_order(client, "SO-QUARANTINE-1", [{"item_revision_id": 1, "quantity": "150"}])
    analysis = client.get(f"/sales-orders/{order['id']}/fulfillment-analysis").json()

    line = analysis["lines"][0]
    # Available stock should ignore quarantine (only 100 eligible from RAW-WH)
    assert Decimal(line["available"]) == Decimal("100")
    assert Decimal(line["ship_from_stock"]) == Decimal("100")
    assert Decimal(line["production_required"]) == Decimal("50")


def test_fulfillment_analysis_multi_line_allocation(client):
    receive(client, "100")
    # Order with two lines for the same SKU
    # RAW-WH has 100 available for PV-550
    order = create_order(client, "SO-MULTILINE-1", [
        {"item_revision_id": 1, "quantity": "70"},
        {"item_revision_id": 1, "quantity": "60"},
    ])
    analysis = client.get(f"/sales-orders/{order['id']}/fulfillment-analysis").json()

    assert Decimal(analysis["total_ordered"]) == Decimal("130")
    assert Decimal(analysis["total_from_stock"]) == Decimal("100")
    assert Decimal(analysis["total_production_required"]) == Decimal("30")

    l1 = analysis["lines"][0]
    assert Decimal(l1["ordered"]) == Decimal("70")
    assert Decimal(l1["available"]) == Decimal("100")
    assert Decimal(l1["ship_from_stock"]) == Decimal("70")
    assert Decimal(l1["production_required"]) == Decimal("0")

    l2 = analysis["lines"][1]
    assert Decimal(l2["ordered"]) == Decimal("60")
    assert Decimal(l2["available"]) == Decimal("30")  # remaining available after l1
    assert Decimal(l2["ship_from_stock"]) == Decimal("30")
    assert Decimal(l2["production_required"]) == Decimal("30")


def test_fulfillment_analysis_not_found(client):
    resp = client.get("/sales-orders/999999/fulfillment-analysis")
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "order_not_found"


@pytest.mark.parametrize("stock,demand,ship,production", [
    ("100", "100", "100", "0"),
    ("100", "100.000001", "100", "0.000001"),
    ("0.000001", "0.000002", "0.000001", "0.000001"),
])
def test_fulfillment_decimal_boundaries(client, stock, demand, ship, production):
    receive(client, stock)
    order = create_order(client, "SO-BOUNDARY", [{"item_revision_id": 1, "quantity": demand}])
    response = client.get(f"/sales-orders/{order['id']}/fulfillment-analysis")
    assert response.status_code == 200
    result = response.json()
    line = result["lines"][0]
    assert isinstance(line["ship_from_stock"], str)
    assert isinstance(line["production_required"], str)
    assert Decimal(line["ship_from_stock"]) == Decimal(ship)
    assert Decimal(line["production_required"]) == Decimal(production)
    assert Decimal(result["total_from_stock"]) + Decimal(result["total_production_required"]) == Decimal(demand)
    assert result["can_fulfill_entirely_from_stock"] == (Decimal(production) == 0)


def test_fulfillment_excludes_active_allocations_and_restores_released_stock(client):
    receive(client, "100")
    owner = create_order(client, "SO-OWNER", [{"item_revision_id": 1, "quantity": "70"}])
    target = create_order(client, "SO-TARGET", [{"item_revision_id": 1, "quantity": "100"}])
    response = client.post("/inventory/reservations", json={
        "item_revision_id": 1, "location_id": 1, "quantity": "70",
        "sales_order_line_id": owner["lines"][0]["id"],
    })
    assert response.status_code == 201
    reservation_id = response.json()["id"]
    path = f"/sales-orders/{target['id']}/fulfillment-analysis"
    result = client.get(path).json()
    assert Decimal(result["total_from_stock"]) == 30
    assert Decimal(result["total_production_required"]) == 70
    assert client.post(f"/inventory/reservations/{reservation_id}/release").status_code == 200
    assert client.get(path).json()["can_fulfill_entirely_from_stock"] is True


@pytest.mark.parametrize("kind,active,expected", [
    ("WAREHOUSE", True, 100), ("BIN", True, 100), ("PRODUCTION", True, 100),
    ("QUARANTINE", True, 0), ("DAMAGED", True, 0), ("TRANSIT", True, 0),
    ("WAREHOUSE", False, 0), ("BIN", False, 0), ("PRODUCTION", False, 0),
])
def test_fulfillment_location_eligibility(client, fulfillment_engine, kind, active, expected):
    with fulfillment_engine.begin() as connection:
        connection.execute(text("UPDATE inventory_locations SET location_type=:kind, is_active=:active WHERE id=1"),
                           {"kind": kind, "active": active})
    receive(client, "100")
    order = create_order(client, "SO-ELIGIBILITY", [{"item_revision_id": 1, "quantity": "100"}])
    result = client.get(f"/sales-orders/{order['id']}/fulfillment-analysis").json()
    assert Decimal(result["total_from_stock"]) == expected
    assert Decimal(result["total_production_required"]) == 100 - expected


def test_fulfillment_empty_order_and_tenant_isolation(client):
    order = create_order(client, "SO-EMPTY", [])
    path = f"/sales-orders/{order['id']}/fulfillment-analysis"
    result = client.get(path).json()
    assert result["lines"] == []
    assert result["can_fulfill_entirely_from_stock"] is False
    assert all(Decimal(result[field]) == 0 for field in ("total_ordered", "total_from_stock", "total_production_required"))
    response = client.get(path, headers={"X-Organization-ID": "2"})
    assert response.status_code == 404
    assert response.json()["detail"]["code"] == "order_not_found"
    client.headers.pop("X-Organization-ID")
    assert client.get(path).status_code == 422


def test_fulfillment_keeps_revisions_separate(client):
    receive(client, "100", revision=1)
    receive(client, "5", revision=2)
    order = create_order(client, "SO-REVISIONS", [
        {"item_revision_id": 1, "quantity": "70"},
        {"item_revision_id": 2, "quantity": "10"},
        {"item_revision_id": 1, "quantity": "60"},
    ])
    result = client.get(f"/sales-orders/{order['id']}/fulfillment-analysis").json()
    assert [Decimal(line["ship_from_stock"]) for line in result["lines"]] == [70, 5, 30]
    assert [Decimal(line["production_required"]) for line in result["lines"]] == [0, 5, 30]
    assert Decimal(result["total_from_stock"]) == 105
    assert Decimal(result["total_production_required"]) == 35
