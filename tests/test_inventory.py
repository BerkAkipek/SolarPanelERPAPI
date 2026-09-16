"""Physical ledger behavior through HTTP against PostgreSQL."""
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


@pytest.fixture(scope="module")
def inventory_schema():
    name = "test_inventory_" + uuid4().hex
    with connect() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        migrate_schema(name)
        with in_schema(name) as connection:
            connection.execute("""
                INSERT INTO organizations(id,code,name) VALUES (1,'A','A'),(2,'B','B');
                INSERT INTO items(id,organization_id,sku,name,item_type,base_uom)
                VALUES (1,1,'CELL','Cell','COMPONENT','EA'),(2,2,'OTHER','Other','MATERIAL','KG');
                INSERT INTO item_revisions(id,organization_id,item_id,revision_code)
                VALUES (1,1,1,'REV-A'),(2,1,1,'REV-B'),(3,2,2,'REV-A');
                INSERT INTO inventory_locations(id,organization_id,code,name,location_type)
                VALUES (1,1,'RAW-WH','Raw warehouse','WAREHOUSE'),
                       (2,1,'PRODUCTION','Production','PRODUCTION'),
                       (3,2,'OTHER','Other organization','WAREHOUSE'),
                       (4,1,'QUARANTINE','Quarantine','QUARANTINE');
            """)
        yield name
    finally:
        with connect() as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture(scope="module")
def api_engine(inventory_schema):
    engine = create_engine("postgresql+psycopg://", creator=lambda: in_schema(inventory_schema))
    yield engine
    engine.dispose()


def line(quantity, *, revision=1, source=None, destination=None):
    return {
        "item_revision_id": revision, "from_location_id": source,
        "to_location_id": destination, "quantity": quantity,
    }


def post(client, kind, lines, *, key=None, reference=None, **kwargs):
    headers = {"Idempotency-Key": key} if key is not None else {}
    return client.post("/inventory/movements", json={
        "movement_type": kind, "reference": reference, "lines": lines, **kwargs,
    }, headers=headers)


def receipt(client, quantity="10000", *, revision=1, destination=1, key=None):
    response = post(client, "RECEIPT", [line(quantity, revision=revision, destination=destination)], key=key)
    assert response.status_code == 201, response.text
    return response


def balance(client, revision=1):
    response = client.get(f"/inventory/items/{revision}/on-hand")
    assert response.status_code == 200, response.text
    result = response.json()
    assert Decimal(result["on_hand_quantity"]) == Decimal(result["incoming_quantity"]) - Decimal(result["outgoing_quantity"])
    for location in result["locations"]:
        assert Decimal(location["on_hand_quantity"]) == Decimal(location["incoming_quantity"]) - Decimal(location["outgoing_quantity"])
    assert sum((Decimal(row["on_hand_quantity"]) for row in result["locations"]), Decimal(0)) == Decimal(result["on_hand_quantity"])
    return result


def assert_error(response, status, code):
    assert response.status_code == status, response.text
    assert response.json()["detail"]["code"] == code


def test_receipt_increases_stock(api, api_connection):
    result = receipt(api).json()
    assert len(result["lines"]) == 1
    assert Decimal(result["lines"][0]["quantity"]) == 10000
    stock = balance(api)
    assert stock["sku"] == "CELL"
    assert stock["revision_code"] == "REV-A"
    assert stock["base_uom"] == "EA"
    assert Decimal(stock["incoming_quantity"]) == 10000
    assert Decimal(stock["outgoing_quantity"]) == 0
    assert Decimal(stock["on_hand_quantity"]) == 10000
    assert stock["locations"][0]["code"] == "RAW-WH"
    assert api_connection.scalar(text("SELECT count(*) FROM stock_reservations")) == 0


def test_transfer_preserves_total_stock(api):
    receipt(api)
    response = post(api, "TRANSFER", [line("500", source=1, destination=2)])
    assert response.status_code == 201, response.text
    result = balance(api)
    assert Decimal(result["on_hand_quantity"]) == 10000
    assert Decimal(result["incoming_quantity"]) == 10500
    assert Decimal(result["outgoing_quantity"]) == 500
    assert {row["code"]: Decimal(row["on_hand_quantity"]) for row in result["locations"]} == {
        "RAW-WH": Decimal(9500), "PRODUCTION": Decimal(500),
    }


def test_outgoing_adjustment_decreases_only_source_stock(api):
    receipt(api, "100")
    receipt(api, "20", destination=2)
    assert post(api, "ADJUSTMENT_OUT", [line("30", source=1)]).status_code == 201
    result = balance(api)
    assert Decimal(result["on_hand_quantity"]) == 90
    assert {row["location_id"]: Decimal(row["on_hand_quantity"]) for row in result["locations"]} == {
        1: Decimal(70), 2: Decimal(20),
    }


def test_positive_adjustment_increases_stock(api):
    assert post(api, "ADJUSTMENT_IN", [line("25", destination=1)]).status_code == 201
    assert Decimal(balance(api)["on_hand_quantity"]) == 25


def test_known_revision_without_movements_is_zero(api):
    result = balance(api)
    assert Decimal(result["on_hand_quantity"]) == 0
    assert result["locations"] == []


def test_revisions_have_separate_balances(api):
    receipt(api, "10", revision=1)
    receipt(api, "7", revision=2)
    assert Decimal(balance(api, 1)["on_hand_quantity"]) == 10
    assert Decimal(balance(api, 2)["on_hand_quantity"]) == 7


def test_physical_stock_includes_quarantine(api):
    receipt(api, "10", destination=4)
    assert Decimal(balance(api)["on_hand_quantity"]) == 10


@pytest.mark.parametrize("quantity", ["-1", "0", "NaN", "Infinity", "0.0000001", "1000000000000"])
def test_cannot_move_invalid_quantity(api, quantity):
    response = post(api, "RECEIPT", [line(quantity, destination=1)])
    assert response.status_code == 422
    assert Decimal(balance(api)["on_hand_quantity"]) == 0


def test_decimal_arithmetic_and_exact_depletion(api):
    receipt(api, "0.1")
    receipt(api, "0.2")
    assert Decimal(balance(api)["on_hand_quantity"]) == Decimal("0.3")
    assert post(api, "ADJUSTMENT_OUT", [line("0.3", source=1)]).status_code == 201
    assert Decimal(balance(api)["on_hand_quantity"]) == 0
    assert_error(post(api, "ADJUSTMENT_OUT", [line("0.000001", source=1)]), 409, "insufficient_stock")


@pytest.mark.parametrize("kind,destination", [("TRANSFER", 2), ("ADJUSTMENT_OUT", None)])
def test_cannot_move_more_than_source_stock(api, kind, destination):
    receipt(api, "10")
    assert_error(post(api, kind, [line("11", source=1, destination=destination)]), 409, "insufficient_stock")
    assert Decimal(balance(api)["on_hand_quantity"]) == 10


def test_stock_in_another_location_does_not_cover_a_shortage(api):
    receipt(api, "100", destination=2)
    assert_error(post(api, "TRANSFER", [line("1", source=1, destination=2)]), 409, "insufficient_stock")


def test_whole_movement_rolls_back_when_second_line_fails(api, api_connection):
    receipt(api, "100")
    headers_before = api_connection.scalar(text("SELECT count(*) FROM inventory_movements"))
    lines_before = api_connection.scalar(text("SELECT count(*) FROM inventory_movement_lines"))
    response = post(api, "TRANSFER", [
        line("40", source=1, destination=2),
        line("70", source=1, destination=2),
    ], key="atomic-transfer")
    assert_error(response, 409, "insufficient_stock")
    stock = balance(api)
    assert Decimal(stock["on_hand_quantity"]) == 100
    assert len(stock["locations"]) == 1
    assert Decimal(stock["locations"][0]["outgoing_quantity"]) == 0
    assert api_connection.scalar(text("SELECT count(*) FROM inventory_movements")) == headers_before
    assert api_connection.scalar(text("SELECT count(*) FROM inventory_movement_lines")) == lines_before
    # Failed posting does not consume the retry key.
    retry = post(api, "TRANSFER", [line("100", source=1, destination=2)], key="atomic-transfer")
    assert retry.status_code == 201, retry.text


def test_second_invalid_direction_rolls_back_first_receipt_line(api, api_connection):
    response = post(api, "RECEIPT", [line("10", destination=1), line("1", source=1)])
    assert_error(response, 422, "invalid_movement_direction")
    assert Decimal(balance(api)["on_hand_quantity"]) == 0
    assert api_connection.scalar(text("SELECT count(*) FROM inventory_movements")) == 0


@pytest.mark.parametrize("kind,source,destination", [
    ("RECEIPT", 1, None), ("RECEIPT", 1, 2), ("RECEIPT", None, None),
    ("ADJUSTMENT_IN", 1, None), ("ADJUSTMENT_OUT", None, 1),
    ("ADJUSTMENT_OUT", 1, 2), ("TRANSFER", 1, None), ("TRANSFER", None, 2),
])
def test_movement_direction_is_validated(api, kind, source, destination):
    assert_error(post(api, kind, [line("1", source=source, destination=destination)]), 422, "invalid_movement_direction")


def test_transfer_requires_distinct_locations(api):
    assert post(api, "TRANSFER", [line("1", source=1, destination=1)]).status_code == 422


def test_requires_lines_and_explicit_supported_type(api):
    assert post(api, "RECEIPT", []).status_code == 422
    assert post(api, "ADJUSTMENT", [line("1", destination=1)]).status_code == 422
    assert post(api, "SHIPMENT", [line("1", source=1)]).status_code == 422


@pytest.mark.parametrize("revision,destination,code", [
    (999999, 1, "revision_not_found"), (3, 1, "revision_not_found"),
    (1, 999999, "location_not_found"), (1, 3, "location_not_found"),
])
def test_missing_and_cross_organization_references(api, revision, destination, code):
    assert_error(post(api, "RECEIPT", [line("1", revision=revision, destination=destination)]), 404, code)


def test_on_hand_cannot_read_other_organization(api):
    assert_error(api.get("/inventory/items/3/on-hand"), 404, "revision_not_found")
    assert_error(api.get("/inventory/items/999999/on-hand"), 404, "revision_not_found")


def test_posting_requires_organization_header(api):
    api.headers.pop("X-Organization-ID")
    assert post(api, "RECEIPT", [line("1", destination=1)]).status_code == 422


def test_retry_with_same_key_returns_original_without_double_posting(api, api_connection):
    first = receipt(api, "10.0", key="receipt-1")
    retry = receipt(api, "10.000000", key="receipt-1")
    assert retry.json() == first.json()
    assert Decimal(balance(api)["on_hand_quantity"]) == 10
    assert api_connection.scalar(text("SELECT count(*) FROM inventory_movements")) == 1


def test_same_key_with_different_request_is_conflict(api):
    receipt(api, "10", key="receipt-1")
    assert_error(post(api, "RECEIPT", [line("11", destination=1)], key="receipt-1"), 409, "idempotency_conflict")
    assert Decimal(balance(api)["on_hand_quantity"]) == 10


def test_without_key_each_post_is_a_new_movement(api):
    receipt(api, "10")
    receipt(api, "10")
    assert Decimal(balance(api)["on_hand_quantity"]) == 20


def test_equivalent_occurred_at_timezones_can_retry(api):
    first = post(api, "RECEIPT", [line("1", destination=1)], key="dated", occurred_at="2026-09-16T10:00:00+03:00")
    assert first.status_code == 201
    retry = post(api, "RECEIPT", [line("1", destination=1)], key="dated", occurred_at="2026-09-16T07:00:00Z")
    assert retry.json() == first.json()
    assert post(api, "RECEIPT", [line("1", destination=1)], occurred_at="2026-09-16T10:00:00").status_code == 422


@pytest.mark.parametrize("mode,revision", [("outgoing", 1), ("retry", 2)])
def test_concurrent_postings_are_serialized(api_engine, mode, revision):
    pids = Queue()

    def committed_session():
        with Session(api_engine, expire_on_commit=False) as session, session.begin():
            session.execute(text("SET LOCAL statement_timeout='8s'"))
            pids.put(session.scalar(text("SELECT pg_backend_pid()")))
            yield session

    app.dependency_overrides[get_session] = committed_session
    try:
        with TestClient(app, headers={"X-Organization-ID": "1"}) as client:
            receipt(client, "10", revision=revision)
            while not pids.empty():
                pids.get_nowait()
            # Hold the same organization lock as posting to prove the request waits.
            with api_engine.connect() as blocker, blocker.begin():
                blocker.execute(text("SELECT pg_advisory_xact_lock(1)"))
                if mode == "outgoing":
                    movement = blocker.scalar(text("""
                        INSERT INTO inventory_movements(organization_id,movement_type)
                        VALUES (1,'ADJUSTMENT_OUT') RETURNING id
                    """))
                    blocker.execute(text("""
                        INSERT INTO inventory_movement_lines(organization_id,movement_id,item_revision_id,from_location_id,quantity)
                        VALUES (1,:movement,:revision,1,6)
                    """), {"movement": movement, "revision": revision})
                with ThreadPoolExecutor(max_workers=2) as pool:
                    if mode == "outgoing":
                        pending = pool.submit(post, client, "ADJUSTMENT_OUT", [line("6", revision=revision, source=1)])
                    else:
                        pending = pool.submit(receipt, client, "1", revision=revision, key="concurrent-retry")
                        simultaneous_retry = pool.submit(receipt, client, "1.000", revision=revision, key="concurrent-retry")
                    try:
                        for _ in range(2 if mode == "retry" else 1):
                            pid = pids.get(timeout=4)
                            deadline = time.monotonic() + 4
                            while time.monotonic() < deadline:
                                if blocker.scalar(text("SELECT cardinality(pg_blocking_pids(:pid)) > 0"), {"pid": pid}):
                                    break
                                time.sleep(0.02)
                            else:
                                pytest.fail("Movement did not wait for the stock lock")
                    finally:
                        blocker.commit()
                    response = pending.result(timeout=10)
                    if mode == "retry":
                        assert simultaneous_retry.result(timeout=10).json() == response.json()
            if mode == "outgoing":
                assert_error(response, 409, "insufficient_stock")
                assert Decimal(balance(client, revision)["on_hand_quantity"]) == 4
            else:
                retry = receipt(client, "1.000", revision=revision, key="concurrent-retry")
                assert retry.json() == response.json()
                assert Decimal(balance(client, revision)["on_hand_quantity"]) == 11
    finally:
        app.dependency_overrides.pop(get_session, None)
