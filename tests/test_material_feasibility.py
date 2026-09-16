"""Tests for Step 7: Material feasibility domain logic and HTTP interface."""
from decimal import Decimal
import os
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.inventory.models import InventoryMovement, StockReservation
from app.main import app
from app.production.domain import (
    ComponentFeasibility,
    MaterialRequirement,
    evaluate_material_feasibility,
)
from tests.db_support import connect, in_schema, migrate_schema


# ---------------------------------------------------------------------------
# Pure domain unit tests
# ---------------------------------------------------------------------------


def test_feasibility_exact_prompt_shortage_example():
    """Prompt's exact scenario:

    Production required: 70
    CELL-M10: required = 10080, available = 12000 -> shortage = 0
    JB-1500:  required = 70,    available = 50    -> shortage = 20
    can_produce: false
    """
    requirements = [
        MaterialRequirement(
            bom_line_id=1,
            line_no=1,
            component_revision_id=10,
            quantity_per_unit=Decimal("144.000000"),
            required_quantity=Decimal("10080.000000"),
            sku="CELL-M10",
            name="Solar Cell M10",
            base_uom="PCS",
        ),
        MaterialRequirement(
            bom_line_id=2,
            line_no=2,
            component_revision_id=11,
            quantity_per_unit=Decimal("1.000000"),
            required_quantity=Decimal("70.000000"),
            sku="JB-1500",
            name="Junction Box 1500V",
            base_uom="PCS",
        ),
    ]

    availabilities = {
        10: Decimal("12000.000000"),
        11: Decimal("50.000000"),
    }

    result = evaluate_material_feasibility(requirements, availabilities, production_required=70)

    assert result.production_required == Decimal("70")
    assert result.can_produce is False
    assert len(result.materials) == 2

    cell = result.materials[0]
    assert cell.sku == "CELL-M10"
    assert cell.required == Decimal("10080.000000")
    assert cell.available == Decimal("12000.000000")
    assert cell.shortage == Decimal("0.000000")

    jb = result.materials[1]
    assert jb.sku == "JB-1500"
    assert jb.required == Decimal("70.000000")
    assert jb.available == Decimal("50.000000")
    assert jb.shortage == Decimal("20.000000")


def test_feasibility_fully_feasible():
    requirements = [
        MaterialRequirement(
            bom_line_id=1,
            line_no=1,
            component_revision_id=10,
            quantity_per_unit=Decimal("144"),
            required_quantity=Decimal("10080"),
            sku="CELL-M10",
        ),
        MaterialRequirement(
            bom_line_id=2,
            line_no=2,
            component_revision_id=11,
            quantity_per_unit=Decimal("1"),
            required_quantity=Decimal("70"),
            sku="JB-1500",
        ),
    ]
    availabilities = {
        10: Decimal("15000"),
        11: Decimal("100"),
    }

    result = evaluate_material_feasibility(requirements, availabilities, 70)
    assert result.can_produce is True
    for mat in result.materials:
        assert mat.shortage == Decimal("0.000000")


def test_feasibility_aggregates_multiple_lines_same_component():
    """If two lines in a BOM use the same component revision, requirements must be aggregated."""
    requirements = [
        MaterialRequirement(
            bom_line_id=1,
            line_no=1,
            component_revision_id=10,
            quantity_per_unit=Decimal("50"),
            required_quantity=Decimal("50"),
            sku="CELL-M10",
        ),
        MaterialRequirement(
            bom_line_id=2,
            line_no=2,
            component_revision_id=10,
            quantity_per_unit=Decimal("20"),
            required_quantity=Decimal("20"),
            sku="CELL-M10",
        ),
    ]
    # Total required = 70. Available = 60. Shortage = 10.
    availabilities = {10: Decimal("60")}

    result = evaluate_material_feasibility(requirements, availabilities, 1)
    assert len(result.materials) == 1
    assert result.materials[0].required == Decimal("70.000000")
    assert result.materials[0].available == Decimal("60.000000")
    assert result.materials[0].shortage == Decimal("10.000000")
    assert result.can_produce is False


def test_feasibility_zero_available_defaults_to_shortage():
    requirements = [
        MaterialRequirement(
            bom_line_id=1,
            line_no=1,
            component_revision_id=99,
            quantity_per_unit=Decimal("10"),
            required_quantity=Decimal("100"),
            sku="RIBBON",
        ),
    ]
    # No entry in availabilities dict
    result = evaluate_material_feasibility(requirements, {}, 10)
    assert result.materials[0].available == Decimal("0.000000")
    assert result.materials[0].shortage == Decimal("100.000000")
    assert result.can_produce is False


@pytest.mark.parametrize("invalid_qty", [0, -1, "-10", "NaN", "Infinity"])
def test_feasibility_rejects_invalid_production_quantity(invalid_qty):
    requirements = [
        MaterialRequirement(
            bom_line_id=1,
            line_no=1,
            component_revision_id=1,
            quantity_per_unit=Decimal("1"),
            required_quantity=Decimal("1"),
            sku="TEST",
        )
    ]
    with pytest.raises(ValueError):
        evaluate_material_feasibility(requirements, {1: Decimal("10")}, invalid_qty)


def test_feasibility_pure_function_has_no_side_effects():
    requirements = [
        MaterialRequirement(
            bom_line_id=1,
            line_no=1,
            component_revision_id=1,
            quantity_per_unit=Decimal("1"),
            required_quantity=Decimal("10"),
            sku="TEST",
        )
    ]
    availabilities = {1: Decimal("5")}
    res1 = evaluate_material_feasibility(requirements, availabilities, 10)
    res2 = evaluate_material_feasibility(requirements, availabilities, 10)

    assert res1.can_produce is False
    assert res2.can_produce is False
    assert availabilities[1] == Decimal("5")  # Dict not mutated


# ---------------------------------------------------------------------------
# Integration / HTTP API tests
# ---------------------------------------------------------------------------

pytestmark = pytest.mark.skipif(
    not os.environ.get("DB_HOST"), reason="Use the Docker Compose test service.",
)


@pytest.fixture(scope="module")
def feasibility_schema():
    name = "test_feas_" + uuid4().hex
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


@pytest.fixture(scope="module")
def feasibility_engine(feasibility_schema):
    engine = create_engine("postgresql+psycopg://", creator=lambda: in_schema(feasibility_schema))
    yield engine
    engine.dispose()


@pytest.fixture
def client(feasibility_engine):
    def test_session():
        with Session(feasibility_engine, expire_on_commit=False) as session, session.begin():
            yield session

    app.dependency_overrides[get_session] = test_session
    with TestClient(app, headers={"X-Organization-ID": "1"}) as test_client:
        yield test_client
    app.dependency_overrides.pop(get_session, None)


def receive(client, revision_id, location_id, quantity):
    resp = client.post("/inventory/movements", json={
        "movement_type": "RECEIPT",
        "lines": [{"item_revision_id": revision_id, "to_location_id": location_id, "quantity": quantity}],
    })
    assert resp.status_code == 201, resp.text


def test_material_feasibility_endpoint_matches_user_prompt(client):
    """Verifies the exact user prompt scenario via HTTP endpoint:

    CELL-M10 in RAW-WH: 12000
    JB-1500  in RAW-WH: 50
    JB-1500  in QA-HOLD: 100 (must NOT be counted)

    GET /production/boms/1/feasibility?quantity=70
    returns:
    {
      "production_required": 70,
      "can_produce": false,
      "materials": [
        {"sku": "CELL-M10", "required": 10080, "available": 12000, "shortage": 0},
        {"sku": "JB-1500",  "required": 70,    "available": 50,    "shortage": 20}
      ]
    }
    """
    # 12000 cells in Warehouse
    receive(client, revision_id=2, location_id=1, quantity="12000")
    # 50 junction boxes in Warehouse
    receive(client, revision_id=3, location_id=1, quantity="50")
    # 100 junction boxes in Quarantine (should NOT be considered reservable/available)
    receive(client, revision_id=3, location_id=2, quantity="100")

    resp = client.get("/production/boms/1/feasibility?quantity=70")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert Decimal(str(data["production_required"])) == Decimal("70")
    assert data["can_produce"] is False

    mats = {m["sku"]: m for m in data["materials"]}
    assert len(mats) == 2

    # CELL-M10
    cell = mats["CELL-M10"]
    assert Decimal(str(cell["required"])) == Decimal("10080")
    assert Decimal(str(cell["available"])) == Decimal("12000")
    assert Decimal(str(cell["shortage"])) == Decimal("0")

    # JB-1500
    jb = mats["JB-1500"]
    assert Decimal(str(jb["required"])) == Decimal("70")
    assert Decimal(str(jb["available"])) == Decimal("50")  # Quarantine stock ignored!
    assert Decimal(str(jb["shortage"])) == Decimal("20")


def test_feasibility_alias_query_param(client):
    """Both ?quantity=70 and ?production_required=70 work."""
    resp = client.get("/production/boms/1/feasibility?production_required=70")
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert Decimal(str(data["production_required"])) == Decimal("70")
    assert data["can_produce"] is False


def test_feasibility_missing_quantity_param(client):
    resp = client.get("/production/boms/1/feasibility")
    assert resp.status_code == 422, resp.text


def test_feasibility_becomes_true_when_shortage_resolved(client):
    # Receive 20 more junction boxes into RAW-WH
    receive(client, revision_id=3, location_id=1, quantity="20")

    resp = client.get("/production/boms/1/feasibility?quantity=70")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["can_produce"] is True
    jb = next(m for m in data["materials"] if m["sku"] == "JB-1500")
    assert Decimal(str(jb["available"])) == Decimal("70")
    assert Decimal(str(jb["shortage"])) == Decimal("0")


def test_feasibility_considers_active_reservations(client):
    # Create an active reservation for 10 JB-1500
    # First create a sales order to own the reservation
    so_resp = client.post("/sales-orders", json={
        "order_number": "SO-RESERVE-FEAS-1",
        "customer_id": 1,
        "currency_code": "EUR",
        "lines": [{"item_revision_id": 3, "quantity": "10"}],
    })
    assert so_resp.status_code == 201, so_resp.text
    so_line_id = so_resp.json()["lines"][0]["id"]

    res_resp = client.post("/inventory/reservations", json={
        "item_revision_id": 3,
        "location_id": 1,
        "quantity": "10",
        "sales_order_line_id": so_line_id,
    })
    assert res_resp.status_code == 201, res_resp.text

    # Now available stock of JB-1500 is 70 - 10 = 60
    resp = client.get("/production/boms/1/feasibility?quantity=70")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["can_produce"] is False
    jb = next(m for m in data["materials"] if m["sku"] == "JB-1500")
    assert Decimal(str(jb["available"])) == Decimal("60")
    assert Decimal(str(jb["shortage"])) == Decimal("10")


def test_feasibility_strictly_read_only(client, feasibility_engine):
    """Feasibility query must NOT alter stock, reservations, or create production orders."""
    with Session(feasibility_engine) as session:
        movements_before = session.scalar(select(text("count(*)")).select_from(InventoryMovement))
        reservations_before = session.scalar(select(text("count(*)")).select_from(StockReservation))

    resp = client.get("/production/boms/1/feasibility?quantity=70")
    assert resp.status_code == 200, resp.text

    with Session(feasibility_engine) as session:
        movements_after = session.scalar(select(text("count(*)")).select_from(InventoryMovement))
        reservations_after = session.scalar(select(text("count(*)")).select_from(StockReservation))

    assert movements_before == movements_after
    assert reservations_before == reservations_after


def test_feasibility_organization_isolation(client):
    # Org 2 attempts to query Org 1's BOM
    with TestClient(app, headers={"X-Organization-ID": "2"}) as org2_client:
        resp = org2_client.get("/production/boms/1/feasibility?quantity=70")
        assert resp.status_code == 404, resp.text


def test_sales_order_material_feasibility_endpoint(client):
    """End-to-end integration:

    1. Finished goods: receive 30 PV-550 panels into RAW-WH.
    2. Customer orders 100 PV-550 panels.
    3. Fulfillment analysis shows ship_from_stock = 30, production_required = 70.
    4. Sales order material feasibility combines this with active BOM 1:
       Evaluates feasibility for the missing 70 panels!
    """
    # Receive 30 finished panels (revision 1)
    receive(client, revision_id=1, location_id=1, quantity="30")

    # Order 100 panels
    so_resp = client.post("/sales-orders", json={
        "order_number": "SO-E2E-FEAS-1",
        "customer_id": 1,
        "currency_code": "EUR",
        "lines": [{"item_revision_id": 1, "quantity": "100"}],
    })
    assert so_resp.status_code == 201, so_resp.text
    order_id = so_resp.json()["id"]

    # Call GET /sales-orders/{order_id}/material-feasibility
    resp = client.get(f"/sales-orders/{order_id}/material-feasibility")
    assert resp.status_code == 200, resp.text
    data = resp.json()

    assert data["sales_order_id"] == order_id
    assert data["order_number"] == "SO-E2E-FEAS-1"
    assert data["can_fulfill_all"] is False

    lines = data["lines"]
    assert len(lines) == 1
    line = lines[0]
    assert line["sku"] == "PV-550"
    assert Decimal(str(line["ordered_quantity"])) == Decimal("100")
    assert Decimal(str(line["ship_from_stock"])) == Decimal("30")
    assert Decimal(str(line["production_required"])) == Decimal("70")
    assert line["bom_id"] == 1
    assert line["can_produce"] is False

    mats = {m["sku"]: m for m in line["materials"]}
    assert Decimal(str(mats["CELL-M10"]["shortage"])) == Decimal("0")
    assert Decimal(str(mats["JB-1500"]["shortage"])) == Decimal("10")
