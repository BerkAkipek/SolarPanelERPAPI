"""Tests for Step 9: Production execution, atomic consumption, and finished-goods output."""
from decimal import Decimal
import os
from uuid import uuid4

from fastapi.testclient import TestClient
from psycopg import sql
import pytest
from sqlalchemy import create_engine, select, text
from sqlalchemy.orm import Session

from app.db import get_session
from app.inventory.models import InventoryMovement, InventoryMovementLine, StockReservation
from app.main import app
from app.production.models import ProductionOrder, ProductionOrderMaterial
from tests.db_support import connect, in_schema, migrate_schema

pytestmark = pytest.mark.skipif(
    not os.environ.get("DB_HOST"), reason="Use the Docker Compose test service.",
)


@pytest.fixture(scope="module")
def exec_schema():
    name = "test_exec_" + uuid4().hex
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
                VALUES (1,1,'RAW-WH','Raw Material Warehouse','WAREHOUSE'),
                       (2,1,'FG-WH','Finished Goods Warehouse','WAREHOUSE'),
                       (3,1,'QA-HOLD','Quarantine Area','QUARANTINE');

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
def exec_engine(exec_schema):
    engine = create_engine("postgresql+psycopg://", creator=lambda: in_schema(exec_schema))
    yield engine
    engine.dispose()


@pytest.fixture
def client(exec_engine):
    def test_session():
        with Session(exec_engine, expire_on_commit=False) as session, session.begin():
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


def get_on_hand(client, revision_id):
    resp = client.get(f"/inventory/items/{revision_id}/on-hand")
    assert resp.status_code == 200, resp.text
    return Decimal(str(resp.json()["on_hand_quantity"]))


def test_full_production_execution_lifecycle(client):
    """Lifecycle:

    DRAFT -> RELEASED -> IN_PROGRESS -> COMPLETED

    Completing production atomically:
    1. Consumes material reservations
    2. Posts PRODUCTION_CONSUMPTION movements
    3. Posts PRODUCTION_OUTPUT finished-goods movement
    4. Marks production order COMPLETED
    """
    # 1. Supply raw materials into RAW-WH (location 1)
    receive(client, revision_id=2, location_id=1, quantity="14400")  # 100 * 144 cells
    receive(client, revision_id=3, location_id=1, quantity="100")    # 100 junction boxes

    assert get_on_hand(client, 2) == Decimal("14400")
    assert get_on_hand(client, 3) == Decimal("100")
    assert get_on_hand(client, 1) == Decimal("0")  # 0 finished goods

    # 2. Create production order for 100 panels
    po_resp = client.post("/production-orders", json={
        "product_revision_id": 1,
        "quantity": "100",
    })
    assert po_resp.status_code == 201, po_resp.text
    po_id = po_resp.json()["id"]
    assert po_resp.json()["status"] == "DRAFT"

    # 3. Release production order -> reserves materials
    rel_resp = client.post(f"/production-orders/{po_id}/release")
    assert rel_resp.status_code == 200, rel_resp.text
    assert rel_resp.json()["status"] == "RELEASED"

    # 4. Start production order -> IN_PROGRESS
    start_resp = client.post(f"/production-orders/{po_id}/start")
    assert start_resp.status_code == 200, start_resp.text
    assert start_resp.json()["status"] == "IN_PROGRESS"

    # 5. Complete production order -> deposit finished goods into FG-WH (location 2)
    comp_resp = client.post(f"/production-orders/{po_id}/complete", json={
        "to_location_id": 2,
    })
    assert comp_resp.status_code == 200, comp_resp.text
    completed = comp_resp.json()

    assert completed["status"] == "COMPLETED"

    # Materials must show consumed_quantity = required_quantity and reserved_quantity = 0
    mats = {m["sku"]: m for m in completed["materials"]}
    assert Decimal(str(mats["CELL-M10"]["consumed_quantity"])) == Decimal("14400")
    assert Decimal(str(mats["CELL-M10"]["reserved_quantity"])) == Decimal("0")
    assert Decimal(str(mats["JB-1500"]["consumed_quantity"])) == Decimal("100")
    assert Decimal(str(mats["JB-1500"]["reserved_quantity"])) == Decimal("0")

    # Verify physical on-hand after atomic completion
    assert get_on_hand(client, 2) == Decimal("0")    # Raw cells consumed to 0!
    assert get_on_hand(client, 3) == Decimal("0")    # Junction boxes consumed to 0!
    assert get_on_hand(client, 1) == Decimal("100")  # 100 Finished PV-550 panels produced in inventory!

    # Verify finished good is in location 2 (FG-WH)
    on_hand_read = client.get("/inventory/items/1/on-hand").json()
    fg_loc = next(l for l in on_hand_read["locations"] if l["location_id"] == 2)
    assert Decimal(str(fg_loc["on_hand_quantity"])) == Decimal("100")

    # 6. Completing already completed order is idempotent
    repeat_comp = client.post(f"/production-orders/{po_id}/complete")
    assert repeat_comp.status_code == 200
    assert repeat_comp.json()["status"] == "COMPLETED"


def test_complete_directly_from_released(client):
    """Can complete an order directly from RELEASED status without calling start."""
    receive(client, revision_id=2, location_id=1, quantity="1440")
    receive(client, revision_id=3, location_id=1, quantity="10")

    po_resp = client.post("/production-orders", json={
        "product_revision_id": 1,
        "quantity": "10",
    })
    po_id = po_resp.json()["id"]
    client.post(f"/production-orders/{po_id}/release")

    # Complete directly
    comp_resp = client.post(f"/production-orders/{po_id}/complete", json={"to_location_id": 2})
    assert comp_resp.status_code == 200
    assert comp_resp.json()["status"] == "COMPLETED"


def test_cannot_complete_draft_or_cancelled_order(client):
    po_resp = client.post("/production-orders", json={
        "product_revision_id": 1,
        "quantity": "10",
    })
    po_id = po_resp.json()["id"]

    # In DRAFT: cannot complete
    resp = client.post(f"/production-orders/{po_id}/complete")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "invalid_order_status"

    # Cancel it
    client.post(f"/production-orders/{po_id}/cancel")

    # Cancelled: cannot complete
    resp2 = client.post(f"/production-orders/{po_id}/complete")
    assert resp2.status_code == 409
    assert resp2.json()["detail"]["code"] == "invalid_order_status"


def test_cannot_start_unreleased_or_cancelled_order(client):
    po_resp = client.post("/production-orders", json={
        "product_revision_id": 1,
        "quantity": "10",
    })
    po_id = po_resp.json()["id"]

    # DRAFT: cannot start
    resp = client.post(f"/production-orders/{po_id}/start")
    assert resp.status_code == 409
    assert resp.json()["detail"]["code"] == "invalid_status"


def test_atomic_rollback_on_failure(client, exec_engine):
    """If completion encounters an error, the ENTIRE transaction rolls back."""
    # Supply stock and release order
    receive(client, revision_id=2, location_id=1, quantity="720")
    receive(client, revision_id=3, location_id=1, quantity="5")

    po_resp = client.post("/production-orders", json={
        "product_revision_id": 1,
        "quantity": "5",
    })
    po_id = po_resp.json()["id"]
    client.post(f"/production-orders/{po_id}/release")

    # Snapshot database state before failed attempt
    with Session(exec_engine) as session:
        movements_before = session.scalar(select(text("count(*)")).select_from(InventoryMovement))
        lines_before = session.scalar(select(text("count(*)")).select_from(InventoryMovementLine))

    # Pass an invalid destination location (e.g. non-existent location ID 9999)
    fail_resp = client.post(f"/production-orders/{po_id}/complete", json={
        "to_location_id": 9999,
    })
    assert fail_resp.status_code == 422

    # Verify everything rolled back completely:
    with Session(exec_engine) as session:
        movements_after = session.scalar(select(text("count(*)")).select_from(InventoryMovement))
        lines_after = session.scalar(select(text("count(*)")).select_from(InventoryMovementLine))

    assert movements_before == movements_after
    assert lines_before == lines_after

    # Order status remains RELEASED (not COMPLETED)
    get_resp = client.get(f"/production-orders/{po_id}")
    assert get_resp.json()["status"] == "RELEASED"
    for m in get_resp.json()["materials"]:
        assert Decimal(str(m["consumed_quantity"])) == Decimal("0")
        assert Decimal(str(m["reserved_quantity"])) > Decimal("0")
