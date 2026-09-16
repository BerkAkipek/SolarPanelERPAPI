"""Integration tests against isolated schemas in real PostgreSQL."""
import os
import time
from concurrent.futures import ThreadPoolExecutor
from uuid import uuid4

import psycopg
from psycopg import sql
import pytest
from tests.db_support import connect, in_schema, migrate_schema

pytestmark = pytest.mark.skipif(
    not os.environ.get("DB_HOST"),
    reason="Database integration tests require DB_HOST; use the Docker Compose test service.",
)


@pytest.fixture(scope="module")
def schema():
    name = "test_" + uuid4().hex
    with connect() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        migrate_schema(name)
        with in_schema(name) as connection:
            seed(connection)
        yield name
    finally:
        with connect() as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


def seed(connection, *, legacy=False):
    statements = """
            INSERT INTO organizations(id,code,name) VALUES (1,'A','A'),(2,'B','B');
            INSERT INTO business_partners(id,organization_id,code,name)
                VALUES (1,1,'CUSTOMER','Customer');
            INSERT INTO items(id,organization_id,sku,name,item_type,base_uom)
                VALUES (1,1,'P','Panel','FINISHED_GOOD','EA'),
                       (2,1,'C','Cell','COMPONENT','EA'),
                       (3,1,'P2','Other panel','FINISHED_GOOD','EA'),
                       (4,2,'P','Other organization panel','FINISHED_GOOD','EA');
            INSERT INTO item_revisions(id,organization_id,item_id,revision_code,status)
                VALUES (1,1,1,'A','ACTIVE'),(2,1,2,'A','ACTIVE'),
                       (3,1,3,'A','ACTIVE'),(4,2,4,'A','ACTIVE');
            INSERT INTO boms(id,organization_id,product_revision_id,version,output_quantity)
                VALUES (1,1,1,1,2);
            INSERT INTO bom_lines(id,organization_id,bom_id,line_no,component_revision_id,quantity)
                VALUES (1,1,1,1,2,3);
            UPDATE boms SET status='ACTIVE' WHERE id=1;
            INSERT INTO inventory_locations(id,organization_id,code,name)
                VALUES (1,1,'MAIN','Main'),(2,2,'MAIN','Other');
            INSERT INTO inventory_locations(id,organization_id,code,name,location_type)
                VALUES (3,1,'QA','Quarantine','QUARANTINE');
            INSERT INTO inventory_movements(id,organization_id,movement_type)
                VALUES (1,1,'RECEIPT'),(2,1,'SHIPMENT'),(3,1,'TRANSFER');
            INSERT INTO inventory_movement_lines(id,organization_id,movement_id,item_revision_id,to_location_id,quantity)
                VALUES (1,1,1,1,1,10),(2,1,1,2,1,10),(3,1,1,1,3,10);
            INSERT INTO sales_orders(id,organization_id,order_number,customer_id,currency_code)
                VALUES (1,1,'SO-1',1,'TRY');
            INSERT INTO sales_order_lines(id,organization_id,sales_order_id,line_no,item_revision_id,quantity)
                VALUES (1,1,1,1,1,100);
            INSERT INTO production_orders(id,organization_id,production_order_number,product_revision_id,bom_id,source_sales_order_line_id,quantity)
                VALUES (1,1,'PO-1',1,1,1,4);
            INSERT INTO production_order_materials(id,organization_id,production_order_id,bom_id,bom_line_id,component_revision_id,required_quantity)
                VALUES (1,1,1,1,1,2,6);
        """
    if legacy:
        statements = statements.replace("'FINISHED_GOOD'", "'PRODUCT'").replace("status='ACTIVE'", "status='APPROVED'")
    connection.execute(statements)


@pytest.fixture
def db(schema):
    connection = in_schema(schema)
    try:
        yield connection
    finally:
        connection.rollback()
        connection.close()


def reserve(db, id=10, quantity=6):
    db.execute("""
        INSERT INTO stock_reservations
            (id,organization_id,item_revision_id,location_id,sales_order_line_id,quantity)
        VALUES (%s,1,1,1,1,%s)
    """, (id, quantity))


def test_available_stock_and_release(db):
    reserve(db)
    assert db.execute("""
        SELECT on_hand_quantity,reserved_quantity,available_quantity
        FROM inventory_availability WHERE item_revision_id=1 AND location_id=1
    """).fetchone() == (10, 6, 4)
    db.execute("UPDATE stock_reservations SET status='RELEASED' WHERE id=10")
    assert db.execute("""
        SELECT available_quantity FROM inventory_availability
        WHERE item_revision_id=1 AND location_id=1
    """).fetchone()[0] == 10


def test_material_reservation(db):
    db.execute("""
        INSERT INTO stock_reservations
            (id,organization_id,item_revision_id,location_id,production_order_material_id,quantity)
        VALUES (10,1,2,1,1,6)
    """)
    assert db.execute("""
        SELECT available_quantity FROM inventory_availability
        WHERE item_revision_id=2 AND location_id=1
    """).fetchone()[0] == 4


@pytest.mark.parametrize("statement,error_type,message", [
    ("INSERT INTO stock_reservations(id,organization_id,item_revision_id,location_id,sales_order_line_id,quantity) VALUES (10,1,1,1,1,11)", psycopg.errors.CheckViolation, "Insufficient available stock"),
    ("INSERT INTO stock_reservations(id,organization_id,item_revision_id,location_id,sales_order_line_id,quantity) VALUES (10,1,1,3,1,1)", psycopg.errors.CheckViolation, "Stock location is not reservable"),
    ("INSERT INTO stock_reservations(id,organization_id,item_revision_id,location_id,sales_order_line_id,quantity) VALUES (10,1,2,1,1,1)", psycopg.errors.ForeignKeyViolation, "foreign key constraint"),
    ("INSERT INTO stock_reservations(id,organization_id,item_revision_id,location_id,quantity) VALUES (10,1,1,1,1)", psycopg.errors.CheckViolation, "check constraint"),
    ("INSERT INTO inventory_movement_lines(id,organization_id,movement_id,item_revision_id,to_location_id,quantity) VALUES (10,1,1,4,1,1)", psycopg.errors.ForeignKeyViolation, "foreign key constraint"),
    ("INSERT INTO inventory_movement_lines(id,organization_id,movement_id,item_revision_id,to_location_id,quantity) VALUES (10,1,1,1,2,1)", psycopg.errors.ForeignKeyViolation, "foreign key constraint"),
    ("INSERT INTO inventory_movement_lines(id,organization_id,movement_id,item_revision_id,to_location_id,quantity) VALUES (10,1,1,1,1,-1)", psycopg.errors.CheckViolation, "check constraint"),
    ("INSERT INTO inventory_movement_lines(id,organization_id,movement_id,item_revision_id,quantity) VALUES (10,1,1,1,1)", psycopg.errors.CheckViolation, "Movement locations"),
    ("INSERT INTO inventory_movement_lines(id,organization_id,movement_id,item_revision_id,from_location_id,quantity) VALUES (10,1,2,1,1,11)", psycopg.errors.CheckViolation, "unavailable stock"),
    ("UPDATE inventory_movement_lines SET quantity=9 WHERE id=1", psycopg.errors.RaiseException, "append-only"),
    ("DELETE FROM inventory_movements WHERE id=1", psycopg.errors.RaiseException, "append-only"),
    ("UPDATE bom_lines SET quantity=5 WHERE id=1", psycopg.errors.ObjectNotInPrerequisiteState, "draft BOM"),
    ("UPDATE boms SET output_quantity=5 WHERE id=1", psycopg.errors.ObjectNotInPrerequisiteState, "immutable"),
    ("UPDATE production_orders SET quantity=5 WHERE id=1", psycopg.errors.RaiseException, "immutable"),
    ("UPDATE production_order_materials SET required_quantity=1 WHERE id=1", psycopg.errors.RaiseException, "append-only"),
    ("INSERT INTO production_orders(id,organization_id,production_order_number,product_revision_id,bom_id,quantity) VALUES (10,1,'BAD',3,1,1)", psycopg.errors.ForeignKeyViolation, "foreign key constraint"),
    ("UPDATE sales_orders SET status='ARBITRARY' WHERE id=1", psycopg.errors.CheckViolation, "check constraint"),
])
def test_invalid_changes_are_rejected(db, statement, error_type, message):
    with pytest.raises(error_type, match=message):
        db.execute(statement)


def test_snapshot_calculation(db):
    db.execute("""
        INSERT INTO production_orders(id,organization_id,production_order_number,product_revision_id,bom_id,quantity)
        VALUES (10,1,'PO-10',1,1,8)
    """)
    with pytest.raises(psycopg.errors.RaiseException, match="Material requirement"):
        db.execute("""
            INSERT INTO production_order_materials
            (id,organization_id,production_order_id,bom_id,bom_line_id,component_revision_id,required_quantity)
            VALUES (10,1,10,1,1,2,8)
        """)


def test_reserved_stock_cannot_be_shipped_without_consuming_reservation(db):
    reserve(db)
    with pytest.raises(psycopg.errors.CheckViolation, match="unavailable"):
        db.execute("""
            INSERT INTO inventory_movement_lines
            (id,organization_id,movement_id,item_revision_id,from_location_id,quantity)
            VALUES (10,1,2,1,1,6)
        """)


def test_consume_and_ship_atomically(db):
    reserve(db)
    db.execute("UPDATE stock_reservations SET status='CONSUMED' WHERE id=10")
    db.execute("""
        INSERT INTO inventory_movements(id,organization_id,movement_type) VALUES (10,1,'SHIPMENT');
        INSERT INTO inventory_movement_lines
        (id,organization_id,movement_id,item_revision_id,from_location_id,quantity)
        VALUES (10,1,10,1,1,6);
    """)
    assert db.execute("""
        SELECT on_hand_quantity,reserved_quantity,available_quantity
        FROM inventory_availability WHERE item_revision_id=1 AND location_id=1
    """).fetchone() == (4, 0, 4)


def wait_until_blocked(observer, pid):
    """Require evidence of contention, not just that a thread started."""
    deadline = time.monotonic() + 4
    while time.monotonic() < deadline:
        blocked = observer.execute(
            "SELECT cardinality(pg_blocking_pids(%s)) > 0", (pid,)
        ).fetchone()[0]
        if blocked:
            return
        time.sleep(0.02)
    pytest.fail("Competing transaction did not block on the stock lock")


@pytest.mark.parametrize("operation,expected,reservation_id", [
    ("reserve", "Insufficient available stock", 100),
    ("reduce_demand", "less than allocated stock", 200),
])
def test_concurrent_allocations_and_demand_changes(schema, operation, expected, reservation_id):
    first = in_schema(schema)
    second = in_schema(schema)
    second.execute("SET statement_timeout='8s'")
    second.commit()
    pid = second.info.backend_pid

    def competitor():
        try:
            if operation == "reserve":
                reserve(second, reservation_id + 1, 6)
            else:
                second.execute("UPDATE sales_order_lines SET quantity=5 WHERE id=1")
        except psycopg.errors.CheckViolation as error:
            second.rollback()
            return str(error)
        return "unexpected success"

    try:
        reserve(first, reservation_id, 6)
        with ThreadPoolExecutor(max_workers=1) as pool:
            pending = pool.submit(competitor)
            try:
                wait_until_blocked(first, pid)
            finally:
                first.commit()
            assert expected in pending.result(timeout=10)
    finally:
        second.rollback()
        second.close()
        first.rollback()
        first.execute("UPDATE stock_reservations SET status='RELEASED' WHERE id=%s", (reservation_id,))
        first.commit()
        first.close()


@pytest.mark.parametrize("status", ["CONSUMED", "RELEASED"])
def test_reservation_cannot_skip_initial_allocation(db, status):
    with pytest.raises(psycopg.errors.CheckViolation, match="must start ACTIVE"):
        db.execute("""
            INSERT INTO stock_reservations
            (id,organization_id,item_revision_id,location_id,sales_order_line_id,quantity,status)
            VALUES (10,1,1,1,1,1,%s)
        """, (status,))


@pytest.mark.parametrize("status", ["CONSUMED", "RELEASED"])
def test_terminal_reservation_cannot_be_reactivated(db, status):
    reserve(db)
    db.execute("UPDATE stock_reservations SET status=%s WHERE id=10", (status,))
    with pytest.raises(psycopg.errors.CheckViolation, match="terminal reservation"):
        db.execute("UPDATE stock_reservations SET status='ACTIVE' WHERE id=10")


@pytest.mark.parametrize("status", ["ACTIVE", "CONSUMED"])
def test_demand_cannot_shrink_below_allocations(db, status):
    reserve(db)
    db.execute("UPDATE stock_reservations SET status=%s WHERE id=10", (status,))
    with pytest.raises(psycopg.errors.CheckViolation, match="less than allocated stock"):
        db.execute("UPDATE sales_order_lines SET quantity=5 WHERE id=1")


def test_released_reservation_allows_demand_reduction(db):
    reserve(db)
    db.execute("UPDATE stock_reservations SET status='RELEASED' WHERE id=10")
    db.execute("UPDATE sales_order_lines SET quantity=1 WHERE id=1")
    assert db.execute("SELECT quantity FROM sales_order_lines WHERE id=1").fetchone()[0] == 1


@pytest.mark.parametrize("quantity", ["0", "-1", "NaN"])
def test_invalid_reservation_quantities(db, quantity):
    with pytest.raises(psycopg.errors.CheckViolation, match="check constraint"):
        reserve(db, quantity=quantity)


def test_exact_stock_boundary_and_retry_after_conflict(db):
    # A failed allocation leaves no record and can be retried after rollback.
    with pytest.raises(psycopg.errors.CheckViolation, match="Insufficient available stock") as error:
        with db.transaction():
            reserve(db, quantity=11)
    assert "organization_id=1" in error.value.diag.message_detail
    assert "quantity=11" in error.value.diag.message_detail
    assert "location_id=1" in error.value.diag.message_detail
    assert db.execute("SELECT count(*) FROM stock_reservations WHERE id=10").fetchone()[0] == 0
    reserve(db, quantity=10)
    assert db.execute("""
        SELECT available_quantity FROM inventory_availability
        WHERE item_revision_id=1 AND location_id=1
    """).fetchone()[0] == 0


def test_failure_rolls_back_consumption_with_its_movement(db):
    reserve(db)
    with pytest.raises(psycopg.errors.CheckViolation, match="unavailable stock"):
        with db.transaction():
            db.execute("UPDATE stock_reservations SET status='CONSUMED' WHERE id=10")
            db.execute("""
                INSERT INTO inventory_movement_lines
                (id,organization_id,movement_id,item_revision_id,from_location_id,quantity)
                VALUES (10,1,2,1,1,11)
            """)
    assert db.execute("SELECT status FROM stock_reservations WHERE id=10").fetchone()[0] == "ACTIVE"
    assert db.execute("""
        SELECT on_hand_quantity,reserved_quantity,available_quantity
        FROM inventory_availability WHERE item_revision_id=1 AND location_id=1
    """).fetchone() == (10, 6, 4)


@pytest.mark.parametrize("kind,source,destination", [
    ("RECEIPT", 1, None),
    ("PRODUCTION_OUTPUT", 1, 3),
    ("SHIPMENT", None, 1),
    ("PRODUCTION_CONSUMPTION", 1, 3),
    ("TRANSFER", None, 1),
    ("TRANSFER", 1, None),
])
def test_movement_type_rejects_wrong_direction(db, kind, source, destination):
    db.execute("""
        INSERT INTO inventory_movements(id,organization_id,movement_type)
        VALUES (10,1,%s)
    """, (kind,))
    with pytest.raises(psycopg.errors.CheckViolation, match="Movement locations"):
        db.execute("""
            INSERT INTO inventory_movement_lines
            (id,organization_id,movement_id,item_revision_id,from_location_id,to_location_id,quantity)
            VALUES (10,1,10,1,%s,%s,1)
        """, (source, destination))


@pytest.mark.parametrize("kind,source,destination", [
    ("RECEIPT", None, 1),
    ("PRODUCTION_OUTPUT", None, 1),
    ("SHIPMENT", 1, None),
    ("PRODUCTION_CONSUMPTION", 1, None),
    ("TRANSFER", 1, 3),
    ("ADJUSTMENT", 1, None),
    ("ADJUSTMENT", None, 1),
])
def test_valid_movement_directions_change_stock(db, kind, source, destination):
    db.execute("""
        INSERT INTO inventory_movements(id,organization_id,movement_type)
        VALUES (10,1,%s)
    """, (kind,))
    db.execute("""
        INSERT INTO inventory_movement_lines
        (id,organization_id,movement_id,item_revision_id,from_location_id,to_location_id,quantity)
        VALUES (10,1,10,1,%s,%s,1)
    """, (source, destination))
    assert db.execute("""
        SELECT on_hand_quantity FROM inventory_availability
        WHERE item_revision_id=1 AND location_id=1
    """).fetchone()[0] == (9 if source == 1 else 11)


def test_missing_movement_has_specific_error(db):
    with pytest.raises(psycopg.errors.ForeignKeyViolation, match="does not exist") as error:
        db.execute("""
            INSERT INTO inventory_movement_lines
            (id,organization_id,movement_id,item_revision_id,to_location_id,quantity)
            VALUES (10,1,999,1,1,1)
        """)
    assert "movement_id=999" in error.value.diag.message_detail


def test_read_only_database_role_cannot_reserve_stock(db):
    # PostgreSQL permission boundary only; this is not an API/RBAC test.
    db.execute("SET LOCAL ROLE pg_read_all_data")
    with pytest.raises(psycopg.errors.InsufficientPrivilege):
        reserve(db)


def test_upgrade_preserves_data_and_can_be_repeated():
    name = "test_upgrade_" + uuid4().hex
    with connect() as connection:
        connection.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
    try:
        migrate_schema(name, "0001_v1")
        with in_schema(name) as connection:
            seed(connection, legacy=True)
            reserve(connection)
        migrate_schema(name)
        migrate_schema(name)
        with in_schema(name) as connection:
            assert connection.execute("SELECT item_type FROM items WHERE id=1").fetchone()[0] == "FINISHED_GOOD"
            assert connection.execute("SELECT status FROM boms WHERE id=1").fetchone()[0] == "ACTIVE"
            assert connection.execute("SELECT quantity,status FROM stock_reservations WHERE id=10").fetchone() == (6, "ACTIVE")
            assert connection.execute("""
                SELECT available_quantity FROM inventory_availability
                WHERE item_revision_id=1 AND location_id=1
            """).fetchone()[0] == 4
    finally:
        with connect() as connection:
            connection.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))
