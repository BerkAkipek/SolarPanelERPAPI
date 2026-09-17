"""Tests for Step 8: Production order creation, material snapshotting, and explicit release."""
from decimal import Decimal
from concurrent.futures import ThreadPoolExecutor
import os
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.orm import Session

from app.db import get_session
from app.inventory.models import StockReservation
from app.main import app
from app.production.models import ProductionOrder, ProductionOrderMaterial
from tests.db_support import connect, in_schema, migrate_schema

pytestmark = pytest.mark.skipif(
    not os.environ.get("DB_HOST"), reason="Use the Docker Compose test service.",
)


@pytest.fixture
def prod_schema():
    name = "test_po_" + uuid4().hex
    with connect() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        migrate_schema(name)
        with in_schema(name) as connection:
            connection.execute("""
                INSERT INTO organizations(id,code,name) VALUES (1,'ORG-A','Org A'),(2,'ORG-B','Org B');
                INSERT INTO business_partners(id,organization_id,code,name)
                VALUES (1,1,'CUST-1','Solar EPC Partner');

                INSERT INTO items(id,organization_id,sku,name,item_type,base_uom)
                VALUES (1,1,'PV-550','550W Solar Panel','FINISHED_GOOD','EA'),
                       (2,1,'CELL-M10','Solar Cell M10','COMPONENT','EA'),
                       (3,1,'JB-1500','Junction Box 1500V','COMPONENT','EA');

                INSERT INTO item_revisions(id,organization_id,item_id,revision_code,status)
                VALUES (1,1,1,'REV-A','ACTIVE'),
                       (2,1,2,'REV-A','ACTIVE'),
                       (3,1,3,'REV-A','ACTIVE');

                INSERT INTO inventory_locations(id,organization_id,code,name,location_type)
                VALUES (1,1,'RAW-WH','Main Warehouse','WAREHOUSE'),
                       (2,1,'QA-HOLD','Quarantine Area','QUARANTINE');

                INSERT INTO boms(id,organization_id,product_revision_id,version,output_quantity)
                VALUES (1,1,1,1,1);
                INSERT INTO bom_lines(id,organization_id,bom_id,line_no,component_revision_id,quantity)
                VALUES (1,1,1,1,2,144),
                       (2,1,1,2,3,1);
                UPDATE boms SET status='ACTIVE' WHERE id=1;
            """)
        yield name
    finally:
        with connect() as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def prod_engine(prod_schema):
    engine = create_engine("postgresql+psycopg://", creator=lambda: in_schema(prod_schema))
    yield engine
    engine.dispose()


@pytest.fixture
def client(prod_engine):
    def test_session():
        with Session(prod_engine, expire_on_commit=False) as session, session.begin():
            yield session

    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app, headers={"X-Organization-ID": "1"}) as test_client:
            yield test_client
    finally:
        app.dependency_overrides.pop(get_session, None)


def receive(client, revision_id, location_id, quantity):
    resp = client.post("/inventory/movements", json={
        "movement_type": "RECEIPT",
        "lines": [{"item_revision_id": revision_id, "to_location_id": location_id, "quantity": quantity}],
    })
    assert resp.status_code == 201, resp.text


def create_sales_order(client, order_number="SO-PROD-1", qty="100"):
    resp = client.post("/sales-orders", json={
        "order_number": order_number,
        "customer_id": 1,
        "currency_code": "EUR",
        "lines": [{"item_revision_id": 1, "quantity": qty}],
    })
    assert resp.status_code == 201, resp.text
    return resp.json()


def test_production_order_creation_snapshots_recipe_in_draft(client):
    """Creating a production order must snapshot:

    - product revision
    - BOM version
    - required quantity
    - material requirements
    within one transaction in DRAFT status, without reserving stock.
    """
    so = create_sales_order(client, "SO-SNAP-1", "100")
    so_line_id = so["lines"][0]["id"]

    # Post production order linked to sales order line for 70 panels
    resp = client.post("/production-orders", json={
        "source_sales_order_line_id": so_line_id,
        "quantity": "70",
    })
    assert resp.status_code == 201, resp.text
    order = resp.json()

    assert order["product_revision_id"] == 1
    assert order["sku"] == "PV-550"
    assert order["bom_id"] == 1
    assert order["bom_version"] == 1
    assert Decimal(str(order["quantity"])) == Decimal("70")
    assert order["status"] == "DRAFT"
    assert order["source_sales_order_line_id"] == so_line_id

    # Verify material requirements snapshot
    mats = {m["sku"]: m for m in order["materials"]}
    assert len(mats) == 2

    cell = mats["CELL-M10"]
    assert Decimal(str(cell["required_quantity"])) == Decimal("10080")
    assert Decimal(str(cell["reserved_quantity"])) == Decimal("0")  # NO reservation in DRAFT!

    jb = mats["JB-1500"]
    assert Decimal(str(jb["required_quantity"])) == Decimal("70")
    assert Decimal(str(jb["reserved_quantity"])) == Decimal("0")


def test_sales_order_nested_production_order_creation(client):
    """POST /sales-orders/{id}/production-orders works as a convenient entrypoint."""
    so = create_sales_order(client, "SO-NESTED-1", "50")
    so_id = so["id"]
    so_line_id = so["lines"][0]["id"]

    resp = client.post(f"/sales-orders/{so_id}/production-orders", json={
        "source_sales_order_line_id": so_line_id,
        "quantity": "50",
    })
    assert resp.status_code == 201, resp.text
    data = resp.json()
    assert data["source_sales_order_line_id"] == so_line_id
    assert Decimal(str(data["quantity"])) == Decimal("50")
    assert data["status"] == "DRAFT"


def test_release_production_order_reserves_materials(client):
    """Explicit release step:

    DRAFT -> RELEASE -> reserve materials
    """
    # Supply sufficient components into RAW-WH (location 1)
    receive(client, revision_id=2, location_id=1, quantity="20000")  # CELL-M10
    receive(client, revision_id=3, location_id=1, quantity="200")    # JB-1500

    so = create_sales_order(client, "SO-REL-1", "70")
    so_line_id = so["lines"][0]["id"]

    # Create production order
    po_resp = client.post("/production-orders", json={
        "source_sales_order_line_id": so_line_id,
        "quantity": "70",
    })
    assert po_resp.status_code == 201, po_resp.text
    po_id = po_resp.json()["id"]

    # Before release: order is DRAFT, zero reserved
    assert po_resp.json()["status"] == "DRAFT"
    for m in po_resp.json()["materials"]:
        assert Decimal(str(m["reserved_quantity"])) == Decimal("0")

    # Release production order
    rel_resp = client.post(f"/production-orders/{po_id}/release")
    assert rel_resp.status_code == 200, rel_resp.text
    released = rel_resp.json()

    assert released["status"] == "RELEASED"
    mats = {m["sku"]: m for m in released["materials"]}
    assert Decimal(str(mats["CELL-M10"]["reserved_quantity"])) == Decimal("10080")
    assert Decimal(str(mats["JB-1500"]["reserved_quantity"])) == Decimal("70")

    # Repeat release is idempotent
    rel_repeat = client.post(f"/production-orders/{po_id}/release")
    assert rel_repeat.status_code == 200
    assert rel_repeat.json()["status"] == "RELEASED"


def test_release_fails_when_stock_insufficient(client):
    """If materials are not available, release must be rejected and order remains DRAFT."""
    so = create_sales_order(client, "SO-NO-STOCK-1", "500")
    so_line_id = so["lines"][0]["id"]

    # Create order for 500 panels (requires 500 * 144 = 72,000 cells, we only have ~9,920 left in stock)
    po_resp = client.post("/production-orders", json={
        "source_sales_order_line_id": so_line_id,
        "quantity": "500",
    })
    assert po_resp.status_code == 201, po_resp.text
    po_id = po_resp.json()["id"]

    # Attempt to release
    rel_resp = client.post(f"/production-orders/{po_id}/release")
    assert rel_resp.status_code == 409, rel_resp.text
    err = rel_resp.json()
    assert err["detail"]["code"] == "insufficient_stock"

    # Order remains DRAFT
    get_resp = client.get(f"/production-orders/{po_id}")
    assert get_resp.status_code == 200
    assert get_resp.json()["status"] == "DRAFT"
    for m in get_resp.json()["materials"]:
        assert Decimal(str(m["reserved_quantity"])) == Decimal("0")


def test_cancel_released_production_order_frees_reservations(client):
    """Cancelling a RELEASED order must restore stock availability by releasing material reservations."""
    # Supply stock
    receive(client, revision_id=2, location_id=1, quantity="1440")
    receive(client, revision_id=3, location_id=1, quantity="10")

    # Make-to-stock order (no sales order line)
    po_resp = client.post("/production-orders", json={
        "product_revision_id": 1,
        "quantity": "10",
    })
    assert po_resp.status_code == 201, po_resp.text
    po_id = po_resp.json()["id"]

    # Release it
    rel_resp = client.post(f"/production-orders/{po_id}/release")
    assert rel_resp.status_code == 200
    assert rel_resp.json()["status"] == "RELEASED"

    # Cancel it
    cancel_resp = client.post(f"/production-orders/{po_id}/cancel")
    assert cancel_resp.status_code == 200
    assert cancel_resp.json()["status"] == "CANCELLED"

    # Reservations must now be RELEASED, meaning reserved_quantity is 0
    get_resp = client.get(f"/production-orders/{po_id}")
    assert get_resp.json()["status"] == "CANCELLED"
    for m in get_resp.json()["materials"]:
        assert Decimal(str(m["reserved_quantity"])) == Decimal("0")


def test_database_triggers_prevent_direct_snapshot_modification(client, prod_engine):
    """Ensures database trigger 'preserve_material_snapshot' protects history against arbitrary SQL UPDATE."""
    po_resp = client.post("/production-orders", json={
        "product_revision_id": 1,
        "quantity": "5",
    })
    assert po_resp.status_code == 201, po_resp.text
    po_id = po_resp.json()["id"]

    with Session(prod_engine) as session:
        mat_id = session.scalar(
            select(ProductionOrderMaterial.id).where(ProductionOrderMaterial.production_order_id == po_id)
        )
        with pytest.raises(Exception, match="append-only"):
            session.execute(
                text(f"UPDATE production_order_materials SET required_quantity = 999 WHERE id = {mat_id}")
            )


def test_production_order_organization_isolation(client):
    po_resp = client.post("/production-orders", json={
        "product_revision_id": 1,
        "quantity": "5",
    })
    assert po_resp.status_code == 201, po_resp.text
    po_id = po_resp.json()["id"]

    with TestClient(app, headers={"X-Organization-ID": "2"}) as org2_client:
        assert org2_client.get(f"/production-orders/{po_id}").status_code == 404
        assert org2_client.post(f"/production-orders/{po_id}/release").status_code == 404


@pytest.mark.parametrize("endpoint", ["/production-orders", "/production/orders"])
def test_direct_creation_persists_snapshot_without_stock_writes(client, prod_engine, endpoint):
    response = client.post(endpoint, json={
        "production_order_number": "PO-DIRECT", "product_revision_id": 1, "bom_id": 1,
        "quantity": "2.5", "planned_start_at": "2026-09-20T09:00:00+03:00",
    })
    assert response.status_code == 201, response.text
    order = response.json()
    assert order["status"] == "DRAFT"
    assert order["source_sales_order_line_id"] is None
    with prod_engine.connect() as connection:
        header = connection.execute(text("""
            SELECT product_revision_id,bom_id,quantity,status FROM production_orders WHERE id=:id
        """), {"id": order["id"]}).one()
        assert tuple(header) == (1, 1, Decimal("2.5"), "DRAFT")
        materials = connection.execute(text("""
            SELECT component_revision_id,required_quantity FROM production_order_materials
            WHERE production_order_id=:id ORDER BY component_revision_id
        """), {"id": order["id"]}).all()
        assert materials == [(2, Decimal("360")), (3, Decimal("2.5"))]
        assert connection.scalar(text("SELECT count(*) FROM stock_reservations")) == 0
        assert connection.scalar(text("SELECT count(*) FROM inventory_movements")) == 0
    assert client.get(f"/production-orders/{order['id']}").json()["materials"] == order["materials"]
    duplicate = client.post(endpoint, json={"production_order_number": "PO-DIRECT", "product_revision_id": 1, "quantity": "2.5"})
    assert duplicate.status_code == 409
    assert duplicate.json()["detail"]["code"] == "duplicate_order_number"


def test_snapshot_survives_new_bom_version(client, prod_engine):
    original = client.post("/production-orders", json={"product_revision_id": 1, "quantity": "2"}).json()
    original = client.get(f"/production-orders/{original['id']}").json()
    with prod_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO boms(id,organization_id,product_revision_id,version,output_quantity)
            VALUES (2,1,1,2,2);
            INSERT INTO bom_lines(id,organization_id,bom_id,line_no,component_revision_id,quantity)
            VALUES (3,1,2,1,2,200);
            UPDATE boms SET status='ACTIVE' WHERE id=2;
        """))
    newest = client.post("/production-orders", json={"product_revision_id": 1, "quantity": "2"}).json()
    assert newest["bom_id"] == 2
    assert Decimal(newest["materials"][0]["required_quantity"]) == 200
    assert client.get(f"/production-orders/{original['id']}").json() == original
    explicit = client.post("/production-orders", json={"product_revision_id": 1, "bom_id": 1, "quantity": "2"})
    assert explicit.status_code == 201
    assert explicit.json()["bom_id"] == 1


def test_nested_creation_resolves_only_the_named_sales_order(client):
    so = create_sales_order(client)
    inferred = client.post(f"/sales-orders/{so['id']}/production-orders", json={"quantity": "5"})
    assert inferred.status_code == 201
    assert inferred.json()["source_sales_order_line_id"] == so["lines"][0]["id"]
    for order_id, headers in [(999999, {}), (so["id"], {"X-Organization-ID": "2"})]:
        response = client.post(f"/sales-orders/{order_id}/production-orders", headers=headers, json={"quantity": "5"})
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "order_not_found"
    other = create_sales_order(client, "SO-OTHER")
    response = client.post(f"/sales-orders/{other['id']}/production-orders", json={
        "source_sales_order_line_id": so["lines"][0]["id"], "quantity": "5",
    })
    assert response.status_code == 404
    assert client.post(f"/sales-orders/{so['id']}/cancel").status_code == 200
    response = client.post("/production-orders", json={"source_sales_order_line_id": so["lines"][0]["id"], "quantity": "5"})
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "order_cancelled"


@pytest.mark.parametrize("payload,status,code", [
    ({"quantity": "1"}, 422, "missing_product_revision"),
    ({"product_revision_id": 999999, "quantity": "1"}, 404, "revision_not_found"),
    ({"product_revision_id": 2, "quantity": "1"}, 422, "finished_good_required"),
    ({"product_revision_id": 1, "bom_id": 999999, "quantity": "1"}, 404, "bom_not_found"),
    ({"product_revision_id": 1, "quantity": "999999999999"}, 422, "material_quantity_out_of_range"),
])
def test_invalid_creation_leaves_no_partial_snapshot(client, prod_engine, payload, status, code):
    response = client.post("/production-orders", json=payload)
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code
    with prod_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM production_orders")) == 0
        assert connection.scalar(text("SELECT count(*) FROM production_order_materials")) == 0


@pytest.mark.parametrize("field,value", [("production_order_number", "  "), ("planned_start_at", "2026-09-20T09:00:00"), ("quantity", "0")])
def test_invalid_production_request_fields(client, field, value):
    response = client.post("/production-orders", json={"product_revision_id": 1, "quantity": "1", field: value})
    assert response.status_code == 422


def test_inactive_product_rejected(client, prod_engine):
    with prod_engine.begin() as connection:
        connection.execute(text("UPDATE items SET is_active=false WHERE id=1"))
    response = client.post("/production-orders", json={"product_revision_id": 1, "quantity": "1"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "item_inactive"


def test_failed_material_insert_rolls_back_header_and_all_materials(client, prod_engine):
    with prod_engine.begin() as connection:
        connection.execute(text("""
            CREATE FUNCTION fail_second_material() RETURNS trigger LANGUAGE plpgsql AS $$
            BEGIN
                IF NEW.component_revision_id=3 THEN
                    RAISE EXCEPTION USING ERRCODE='23514', MESSAGE='Injected material failure';
                END IF;
                RETURN NEW;
            END $$;
            CREATE TRIGGER fail_second_material BEFORE INSERT ON production_order_materials
            FOR EACH ROW EXECUTE FUNCTION fail_second_material();
        """))
    payload = {"production_order_number": "PO-RETRY", "product_revision_id": 1, "quantity": "1"}
    assert client.post("/production-orders", json=payload).status_code == 422
    with prod_engine.begin() as connection:
        assert connection.scalar(text("SELECT count(*) FROM production_orders")) == 0
        assert connection.scalar(text("SELECT count(*) FROM production_order_materials")) == 0
        connection.execute(text("DROP TRIGGER fail_second_material ON production_order_materials"))
    assert client.post("/production-orders", json=payload).status_code == 201


def test_concurrent_creation_generates_distinct_order_numbers(client):
    def create(_):
        return client.post("/production-orders", json={"product_revision_id": 1, "quantity": "1"})
    with ThreadPoolExecutor(max_workers=3) as pool:
        responses = list(pool.map(create, range(3)))
    assert all(response.status_code == 201 for response in responses), [r.text for r in responses]
    assert len({response.json()["production_order_number"] for response in responses}) == 3


@pytest.mark.parametrize("output,component,quantity,expected", [
    ("3", "1", "2", "0.666667"),
    ("2", "0.000001", "1", "0.000001"),
    ("1", "999999999999.999999", "1", "999999999999.999999"),
])
def test_material_snapshot_matches_database_formula_and_every_bom_line(client, prod_engine, output, component, quantity, expected):
    with prod_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO boms(id,organization_id,product_revision_id,version,output_quantity)
            VALUES (2,1,1,2,:output)
        """), {"output": Decimal(output)})
        connection.execute(text("""
            INSERT INTO bom_lines(id,organization_id,bom_id,line_no,component_revision_id,quantity)
            VALUES (3,1,2,20,2,:component), (4,1,2,10,3,2)
        """), {"component": Decimal(component)})
        connection.execute(text("UPDATE boms SET status='ACTIVE' WHERE id=2"))
    response = client.post("/production-orders", json={"product_revision_id": 1, "bom_id": 2, "quantity": quantity})
    assert response.status_code == 201, response.text
    order = response.json()
    by_component = {material["component_revision_id"]: material for material in order["materials"]}
    assert Decimal(by_component[2]["required_quantity"]) == Decimal(expected)
    assert by_component[2]["bom_line_id"] == 3
    assert by_component[3]["bom_line_id"] == 4
    with prod_engine.connect() as connection:
        rows = connection.execute(text("""
            SELECT l.id, m.required_quantity,
                   round(p.quantity*l.quantity/b.output_quantity,6) AS calculated
            FROM production_orders p JOIN boms b ON b.id=p.bom_id
            JOIN bom_lines l ON l.bom_id=b.id
            LEFT JOIN production_order_materials m
              ON m.production_order_id=p.id AND m.bom_line_id=l.id
            WHERE p.id=:id ORDER BY l.line_no
        """), {"id": order["id"]}).all()
        assert len(rows) == 2
        assert all(row.required_quantity == row.calculated for row in rows)
    assert client.get(f"/production-orders/{order['id']}").json()["materials"] == order["materials"]


@pytest.mark.parametrize("operation", ["update", "delete", "duplicate", "wrong_quantity", "wrong_component"])
def test_database_rejects_inconsistent_material_snapshots(client, prod_engine, operation):
    response = client.post("/production-orders", json={"product_revision_id": 1, "quantity": "1"})
    assert response.status_code == 201
    order = response.json()
    material = next(m for m in order["materials"] if m["component_revision_id"] == 2)
    with prod_engine.connect() as connection:
        with pytest.raises(DBAPIError):
            with connection.begin():
                if operation == "update":
                    connection.execute(text("UPDATE production_order_materials SET required_quantity=1 WHERE id=:id"), {"id": material["id"]})
                elif operation == "delete":
                    connection.execute(text("DELETE FROM production_order_materials WHERE id=:id"), {"id": material["id"]})
                else:
                    connection.execute(text("""
                        INSERT INTO production_order_materials
                        (organization_id,production_order_id,bom_id,bom_line_id,component_revision_id,required_quantity)
                        VALUES (1,:order_id,1,1,:component,:quantity)
                    """), {"order_id": order["id"], "component": 3 if operation == "wrong_component" else 2,
                             "quantity": 1 if operation == "wrong_quantity" else 144})
    assert client.get(f"/production-orders/{order['id']}").json()["materials"] == order["materials"]


def test_requirement_rounding_to_zero_rejects_entire_snapshot(client, prod_engine):
    with prod_engine.begin() as connection:
        connection.execute(text("""
            INSERT INTO boms(id,organization_id,product_revision_id,version,output_quantity)
            VALUES (2,1,1,2,3);
            INSERT INTO bom_lines(id,organization_id,bom_id,line_no,component_revision_id,quantity)
            VALUES (3,1,2,1,2,1), (4,1,2,2,3,0.000001);
            UPDATE boms SET status='ACTIVE' WHERE id=2;
        """))
    response = client.post("/production-orders", json={"product_revision_id": 1, "quantity": "1"})
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "material_quantity_out_of_range"
    with prod_engine.connect() as connection:
        assert connection.scalar(text("SELECT count(*) FROM production_orders")) == 0
        assert connection.scalar(text("SELECT count(*) FROM production_order_materials")) == 0
