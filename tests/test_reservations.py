"""Integration tests for Step 3: Stock availability and reservations."""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from decimal import Decimal
from queue import Queue
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
def reservation_schema():
    name = "test_res_" + uuid4().hex
    with connect() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        migrate_schema(name)
        with in_schema(name) as connection:
            connection.execute("""
                INSERT INTO organizations(id,code,name) VALUES (1,'A','A'),(2,'B','B');
                INSERT INTO business_partners(id,organization_id,code,name)
                VALUES (1,1,'CUST-1','Customer One'),(2,2,'CUST-2','Customer Two');
                INSERT INTO items(id,organization_id,sku,name,item_type,base_uom)
                VALUES (1,1,'PV-550','Solar Panel 550W','FINISHED_GOOD','EA'),
                       (2,1,'CELL-1','Solar Cell','COMPONENT','EA'),
                       (3,2,'OTHER','Other Org Item','FINISHED_GOOD','EA');
                INSERT INTO item_revisions(id,organization_id,item_id,revision_code)
                VALUES (1,1,1,'REV-A'),(2,1,2,'REV-A'),(3,2,3,'REV-A');
                INSERT INTO inventory_locations(id,organization_id,code,name,location_type)
                VALUES (1,1,'RAW-WH','Raw warehouse','WAREHOUSE'),
                       (2,1,'PRODUCTION','Production floor','PRODUCTION'),
                       (3,1,'QA','Quarantine','QUARANTINE'),
                       (4,2,'OTHER-WH','Other warehouse','WAREHOUSE');
                INSERT INTO sales_orders(id,organization_id,order_number,customer_id,currency_code)
                VALUES (1,1,'SO-100',1,'EUR'),(2,1,'SO-200',1,'EUR');
                INSERT INTO sales_order_lines(id,organization_id,sales_order_id,line_no,item_revision_id,quantity)
                VALUES (1,1,1,1,1,1000),
                       (2,1,2,1,2,500);
            """)
        yield name
    finally:
        with connect() as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def reservation_engine(reservation_schema):
    engine = create_engine("postgresql+psycopg://", creator=lambda: in_schema(reservation_schema))
    yield engine
    engine.dispose()


@pytest.fixture
def reservation_connection(reservation_engine):
    with reservation_engine.connect() as connection, connection.begin():
        yield connection
        connection.rollback()


@pytest.fixture
def client(reservation_connection):
    def test_session():
        with Session(bind=reservation_connection, join_transaction_mode="create_savepoint",
                     expire_on_commit=False) as session, session.begin():
            yield session

    app.dependency_overrides[get_session] = test_session
    try:
        with TestClient(app, headers={"X-Organization-ID": "1"}) as test_client:
            receive_stock(test_client, "100")
            yield test_client
    finally:
        app.dependency_overrides.pop(get_session, None)


def receive_stock(client, quantity="100", revision=1, location=1):
    response = client.post("/inventory/movements", json={
        "movement_type": "RECEIPT",
        "lines": [{
            "item_revision_id": revision,
            "to_location_id": location,
            "quantity": quantity,
        }],
    })
    assert response.status_code == 201, response.text
    return response.json()


def get_availability(client, revision=1):
    response = client.get(f"/inventory/items/{revision}/availability")
    assert response.status_code == 200, response.text
    return response.json()


def create_res(client, quantity="70", revision=1, location=1, sales_order_line_id=1):
    return client.post("/inventory/reservations", json={
        "item_revision_id": revision,
        "location_id": location,
        "quantity": quantity,
        "sales_order_line_id": sales_order_line_id,
    })


def test_reserve_valid_quantity(client):
    # Each test starts with 100 units.
    avail_before = get_availability(client)
    assert Decimal(avail_before["on_hand"]) == Decimal("100")
    assert Decimal(avail_before["reserved"]) == Decimal("0")
    assert Decimal(avail_before["available"]) == Decimal("100")

    # Reserve 70 units
    res_response = create_res(client, "70")
    assert res_response.status_code == 201, res_response.text
    reservation = res_response.json()
    assert reservation["status"] == "ACTIVE"
    assert Decimal(reservation["quantity"]) == Decimal("70")
    assert reservation["sales_order_line_id"] == 1

    # Availability is now: on_hand=100, reserved=70, available=30
    avail_after = get_availability(client)
    assert Decimal(avail_after["on_hand"]) == Decimal("100")
    assert Decimal(avail_after["reserved"]) == Decimal("70")
    assert Decimal(avail_after["available"]) == Decimal("30")

    # Clean up for subsequent tests
    client.delete(f"/inventory/reservations/{reservation['id']}")


def test_reject_reservation_greater_than_available(client):
    # Available is 100 from this test's fixture.
    avail = get_availability(client)
    current_avail = Decimal(avail["available"])

    # Attempt to reserve more than available
    response = create_res(client, str(current_avail + Decimal("1")))
    assert response.status_code == 409, response.text
    assert response.json()["detail"]["code"] == "insufficient_stock"

    # Availability remains unchanged
    avail_after = get_availability(client)
    assert Decimal(avail_after["available"]) == current_avail


def test_release_reservation_restores_availability(client):
    res_response = create_res(client, "40")
    assert res_response.status_code == 201
    res_id = res_response.json()["id"]

    avail_reserved = get_availability(client)
    assert Decimal(avail_reserved["reserved"]) == Decimal("40")
    assert Decimal(avail_reserved["available"]) == Decimal("60")

    # Soft release via DELETE
    del_response = client.delete(f"/inventory/reservations/{res_id}")
    assert del_response.status_code == 200, del_response.text
    released = del_response.json()
    assert released["status"] == "RELEASED"

    # Availability fully restored
    avail_restored = get_availability(client)
    assert Decimal(avail_restored["reserved"]) == Decimal("0")
    assert Decimal(avail_restored["available"]) == Decimal("100")


def test_consumed_reservation_does_not_become_available(client):
    res_response = create_res(client, "50")
    assert res_response.status_code == 201
    res_id = res_response.json()["id"]

    # Consume reservation
    consume_response = client.post(f"/inventory/reservations/{res_id}/consume")
    assert consume_response.status_code == 200, consume_response.text
    assert consume_response.json()["status"] == "CONSUMED"

    # Consumption posts the physical issue atomically; retries cannot issue twice.
    retry = client.post(f"/inventory/reservations/{res_id}/consume")
    assert retry.status_code == 200
    assert retry.json() == consume_response.json()

    # Available stock does not regain the 50 units
    avail = get_availability(client)
    assert Decimal(avail["on_hand"]) == Decimal("50")
    assert Decimal(avail["reserved"]) == Decimal("0")
    assert Decimal(avail["available"]) == Decimal("50")

    # Attempting to release a CONSUMED reservation must fail
    del_response = client.delete(f"/inventory/reservations/{res_id}")
    assert del_response.status_code == 409
    assert del_response.json()["detail"]["code"] == "reservation_terminal"

    # Replenish stock back to 100 for remaining tests
    receive_stock(client, "50")


def test_repeated_release_is_safe(client):
    res_response = create_res(client, "25")
    assert res_response.status_code == 201
    res_id = res_response.json()["id"]

    first_release = client.delete(f"/inventory/reservations/{res_id}")
    assert first_release.status_code == 200
    assert first_release.json()["status"] == "RELEASED"

    # Second release call is safe and idempotent
    second_release = client.delete(f"/inventory/reservations/{res_id}")
    assert second_release.status_code == 200
    assert second_release.json()["status"] == "RELEASED"

    avail = get_availability(client)
    assert Decimal(avail["reserved"]) == Decimal("0")
    assert Decimal(avail["available"]) == Decimal("100")


def test_two_concurrent_reservations_cannot_oversell_stock(reservation_engine):
    """Concurrency test: Two simultaneous requests competing for stock cannot oversell."""
    pids = Queue()

    def committed_session():
        with Session(reservation_engine, expire_on_commit=False) as session, session.begin():
            session.execute(text("SET LOCAL statement_timeout='8s'"))
            pids.put(session.scalar(text("SELECT pg_backend_pid()")))
            yield session

    app.dependency_overrides[get_session] = committed_session
    try:
        with TestClient(app, headers={"X-Organization-ID": "1"}) as test_client:
            receive_stock(test_client, "100")
            # Current available stock is 100
            avail = get_availability(test_client)
            assert Decimal(avail["available"]) == Decimal("100")

            while not pids.empty():
                pids.get_nowait()

            # Acquire organization advisory lock to block workers and align them
            with reservation_engine.connect() as blocker, blocker.begin():
                blocker.execute(text("SELECT pg_advisory_xact_lock(1)"))

                with ThreadPoolExecutor(max_workers=2) as pool:
                    # Both attempt to reserve 60 units (60 + 60 = 120 > 100)
                    fut1 = pool.submit(create_res, test_client, "60")
                    fut2 = pool.submit(create_res, test_client, "60")

                    # Wait until both workers are waiting on the lock
                    for _ in range(2):
                        pid = pids.get(timeout=4)
                        deadline = time.monotonic() + 4
                        while time.monotonic() < deadline:
                            if blocker.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}):
                                break
                            time.sleep(0.02)
                        else:
                            pytest.fail("Reservation worker did not wait for the organization lock")

                    # Release blocker lock
                    blocker.commit()

                resp1 = fut1.result(timeout=10)
                resp2 = fut2.result(timeout=10)

            # Exactly one must succeed (201) and one must fail (409 insufficient_stock)
            statuses = sorted([resp1.status_code, resp2.status_code])
            assert statuses == [201, 409], f"Unexpected responses: {resp1.text}, {resp2.text}"

            # Verify no overselling: reserved=60, available=40
            avail_final = get_availability(test_client)
            assert Decimal(avail_final["on_hand"]) == Decimal("100")
            assert Decimal(avail_final["reserved"]) == Decimal("60")
            assert Decimal(avail_final["available"]) == Decimal("40")
            winner = resp1 if resp1.status_code == 201 else resp2
            assert test_client.post(f"/inventory/reservations/{winner.json()['id']}/consume").status_code == 200
            response = test_client.post("/inventory/movements", json={
                "movement_type": "ADJUSTMENT_OUT",
                "lines": [{"item_revision_id": 1, "from_location_id": 1, "quantity": "40"}],
            })
            assert response.status_code == 201
    finally:
        app.dependency_overrides.pop(get_session, None)


def test_cannot_reserve_from_quarantine_location(client):
    # Location 3 is QUARANTINE
    response = create_res(client, "10", location=3)
    assert response.status_code == 422
    assert response.json()["detail"]["code"] == "location_not_reservable"


def test_reservation_validation_rules(client):
    # Missing owner (neither sales_order_line_id nor production_order_material_id)
    resp = client.post("/inventory/reservations", json={
        "item_revision_id": 1,
        "location_id": 1,
        "quantity": "10",
    })
    assert resp.status_code == 422

    # Both owners provided
    resp = client.post("/inventory/reservations", json={
        "item_revision_id": 1,
        "location_id": 1,
        "quantity": "10",
        "sales_order_line_id": 1,
        "production_order_material_id": 1,
    })
    assert resp.status_code == 422

    # Negative quantity
    resp = client.post("/inventory/reservations", json={
        "item_revision_id": 1,
        "location_id": 1,
        "quantity": "-5",
        "sales_order_line_id": 1,
    })
    assert resp.status_code == 422

    # Non-existent item revision
    resp = create_res(client, "10", revision=999999)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "revision_not_found"

    # Non-existent location
    resp = create_res(client, "10", location=999999)
    assert resp.status_code == 404
    assert resp.json()["detail"]["code"] == "location_not_found"


def test_mismatched_revision_for_sales_order_line(client):
    # Sales order line 1 is for item_revision_id=1. Reserving item_revision_id=2 against line 1 must fail.
    receive_stock(client, "50", revision=2, location=1)
    resp = client.post("/inventory/reservations", json={
        "item_revision_id": 2,
        "location_id": 1,
        "quantity": "10",
        "sales_order_line_id": 1,
    })
    assert resp.status_code == 422
    assert resp.json()["detail"]["code"] == "sales_order_line_not_found"


def test_explicit_release_preserves_history_and_rejects_consumption(client):
    created = create_res(client, "100").json()
    path = f"/inventory/reservations/{created['id']}"
    assert Decimal(get_availability(client)["available"]) == 0
    released = client.post(path + "/release")
    assert released.status_code == 200
    assert released.json()["status"] == "RELEASED"
    assert client.post(path + "/release").json() == released.json()
    assert client.delete(path).json() == released.json()
    assert client.get(path).json() == released.json()
    assert released.json()["created_at"] == created["created_at"]
    response = client.post(path + "/consume")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "reservation_terminal"
    assert Decimal(get_availability(client)["available"]) == 100


@pytest.mark.parametrize("operation", ["read", "release", "consume", "delete"])
def test_reservation_scope_and_missing_records(client, operation):
    reservation = create_res(client, "10").json()
    for reservation_id, headers in [(reservation["id"], {"X-Organization-ID": "2"}), (999999, {})]:
        path = f"/inventory/reservations/{reservation_id}"
        if operation == "read":
            response = client.get(path, headers=headers)
        elif operation == "delete":
            response = client.delete(path, headers=headers)
        else:
            response = client.post(path + "/" + operation, headers=headers)
        assert response.status_code == 404
        assert response.json()["detail"]["code"] == "reservation_not_found"
    assert client.get(f"/inventory/reservations/{reservation['id']}").json()["status"] == "ACTIVE"


def test_consumption_rolls_back_if_stock_posting_fails(client, reservation_connection):
    reservation = create_res(client, "40").json()
    reservation = client.get(f"/inventory/reservations/{reservation['id']}").json()
    before = get_availability(client)
    # Inject a database failure after the allocation changes but before the issue commits.
    reservation_connection.execute(text("""
        CREATE FUNCTION fail_reservation_issue() RETURNS trigger LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION USING ERRCODE = '23514', MESSAGE = 'Injected issue failure';
        END $$;
        CREATE TRIGGER fail_reservation_issue BEFORE INSERT ON inventory_movement_lines
        FOR EACH ROW EXECUTE FUNCTION fail_reservation_issue();
    """))
    path = f"/inventory/reservations/{reservation['id']}"
    response = client.post(path + "/consume")
    assert response.status_code == 422
    assert client.get(path).json() == reservation
    assert get_availability(client) == before
    assert reservation_connection.scalar(text("SELECT count(*) FROM inventory_movements")) == 1
    reservation_connection.execute(text("DROP TRIGGER fail_reservation_issue ON inventory_movement_lines"))
    assert client.post(path + "/consume").status_code == 200
    assert Decimal(get_availability(client)["on_hand"]) == 60


def test_consumption_cannot_reallocate_issued_stock(client, reservation_connection):
    reservation = create_res(client, "100").json()
    path = f"/inventory/reservations/{reservation['id']}/consume"
    assert client.post(path).status_code == 200
    assert client.post(path).status_code == 200
    response = create_res(client, "0.000001")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "insufficient_stock"
    assert Decimal(get_availability(client)["available"]) == 0
    issues = reservation_connection.execute(text("""
        SELECT m.movement_type, m.reference, l.quantity FROM inventory_movements m
        JOIN inventory_movement_lines l ON l.movement_id = m.id
        WHERE m.movement_type = 'SHIPMENT'
    """)).all()
    assert issues == [("SHIPMENT", f"RESERVATION-{reservation['id']}", Decimal("100"))]


def test_material_reservation_consumes_component_stock(client, reservation_connection):
    reservation_connection.execute(text("""
        INSERT INTO boms(id,organization_id,product_revision_id,version,output_quantity)
        VALUES (1,1,1,1,1);
        INSERT INTO bom_lines(id,organization_id,bom_id,line_no,component_revision_id,quantity)
        VALUES (1,1,1,1,2,10);
        UPDATE boms SET status='ACTIVE' WHERE id=1;
        INSERT INTO production_orders(id,organization_id,production_order_number,product_revision_id,bom_id,quantity)
        VALUES (1,1,'PO-TEST',1,1,1);
        INSERT INTO production_order_materials(organization_id,production_order_id,bom_id,bom_line_id,component_revision_id,required_quantity)
        VALUES (1,1,1,1,2,10);
    """))
    material_id = reservation_connection.scalar(text("SELECT id FROM production_order_materials"))
    receive_stock(client, "10", revision=2)
    response = client.post("/inventory/reservations", json={
        "item_revision_id": 2, "location_id": 1, "quantity": "10",
        "production_order_material_id": material_id,
    })
    assert response.status_code == 201, response.text
    reservation_id = response.json()["id"]
    assert client.post(f"/inventory/reservations/{reservation_id}/consume").status_code == 200
    assert Decimal(get_availability(client, revision=2)["on_hand"]) == 0
    assert reservation_connection.scalar(text("""
        SELECT movement_type FROM inventory_movements WHERE reference = :reference
    """), {"reference": f"RESERVATION-{reservation_id}"}) == "PRODUCTION_CONSUMPTION"


def test_reservations_cannot_exceed_owner_demand(client, reservation_connection):
    reservation_connection.execute(text("UPDATE sales_order_lines SET quantity=10 WHERE id=1"))
    assert create_res(client, "10").status_code == 201
    response = create_res(client, "0.000001")
    assert response.status_code == 409
    assert response.json()["detail"]["code"] == "reservation_exceeds_demand"
    assert Decimal(get_availability(client)["reserved"]) == 10
