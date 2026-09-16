-- Keep ADJUSTMENT for historical records; new API postings use explicit directions.
ALTER TABLE inventory_movements DROP CONSTRAINT inventory_movements_movement_type_check;
ALTER TABLE inventory_movements ADD CONSTRAINT inventory_movements_movement_type_check
CHECK (movement_type IN (
    'RECEIPT', 'TRANSFER', 'ADJUSTMENT_IN', 'ADJUSTMENT_OUT', 'ADJUSTMENT',
    'PRODUCTION_CONSUMPTION', 'PRODUCTION_OUTPUT', 'SHIPMENT'
));

ALTER TABLE inventory_movements
    ADD COLUMN idempotency_key VARCHAR(100),
    ADD COLUMN request_hash CHAR(64),
    ADD CONSTRAINT uq_movement_idempotency UNIQUE (organization_id, idempotency_key),
    ADD CONSTRAINT movement_idempotency_pair CHECK (
        (idempotency_key IS NULL AND request_hash IS NULL)
        OR (idempotency_key IS NOT NULL AND request_hash IS NOT NULL)
    );

CREATE VIEW inventory_on_hand AS
WITH entries AS (
    SELECT organization_id, item_revision_id, to_location_id AS location_id,
           quantity AS incoming_quantity, 0::numeric AS outgoing_quantity
    FROM inventory_movement_lines WHERE to_location_id IS NOT NULL
    UNION ALL
    SELECT organization_id, item_revision_id, from_location_id,
           0::numeric, quantity
    FROM inventory_movement_lines WHERE from_location_id IS NOT NULL
)
SELECT organization_id, item_revision_id, location_id,
       sum(incoming_quantity) AS incoming_quantity,
       sum(outgoing_quantity) AS outgoing_quantity,
       sum(incoming_quantity) - sum(outgoing_quantity) AS on_hand_quantity
FROM entries GROUP BY organization_id, item_revision_id, location_id;

-- Keep existing stock safeguards, with a single authoritative physical-balance formula.
CREATE OR REPLACE VIEW inventory_availability AS
WITH reserved AS (
    SELECT organization_id, item_revision_id, location_id, sum(quantity) AS reserved_quantity
    FROM stock_reservations WHERE status = 'ACTIVE'
    GROUP BY organization_id, item_revision_id, location_id
)
SELECT coalesce(s.organization_id, r.organization_id) AS organization_id,
       coalesce(s.item_revision_id, r.item_revision_id) AS item_revision_id,
       coalesce(s.location_id, r.location_id) AS location_id,
       coalesce(s.on_hand_quantity, 0) AS on_hand_quantity,
       coalesce(r.reserved_quantity, 0) AS reserved_quantity,
       coalesce(s.on_hand_quantity, 0) - coalesce(r.reserved_quantity, 0) AS available_quantity
FROM inventory_on_hand s FULL JOIN reserved r USING (organization_id, item_revision_id, location_id);

CREATE OR REPLACE FUNCTION validate_movement_direction() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kind text;
BEGIN
    SELECT movement_type INTO kind FROM inventory_movements
    WHERE id = NEW.movement_id AND organization_id = NEW.organization_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23503',
            MESSAGE = 'Inventory movement does not exist in this organization',
            DETAIL = format('movement_id=%s organization_id=%s', NEW.movement_id, NEW.organization_id);
    END IF;
    IF (kind IN ('RECEIPT', 'PRODUCTION_OUTPUT', 'ADJUSTMENT_IN') AND
        (NEW.from_location_id IS NOT NULL OR NEW.to_location_id IS NULL))
       OR (kind IN ('SHIPMENT', 'PRODUCTION_CONSUMPTION', 'ADJUSTMENT_OUT') AND
           (NEW.from_location_id IS NULL OR NEW.to_location_id IS NOT NULL))
       OR (kind = 'TRANSFER' AND
           (NEW.from_location_id IS NULL OR NEW.to_location_id IS NULL)) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'inventory_movement_direction',
            MESSAGE = 'Movement locations do not match the movement type',
            DETAIL = format('movement_id=%s movement_type=%s from_location_id=%s to_location_id=%s',
                            NEW.movement_id, kind, NEW.from_location_id, NEW.to_location_id),
            HINT = 'Receipts and positive adjustments need a destination; issues and negative adjustments a source; transfers both.';
    END IF;
    RETURN NEW;
END $$;

-- Give ledger failures a stable API error without changing reservation operations.
CREATE FUNCTION validate_movement_balance() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE balance numeric;
BEGIN
    IF NEW.from_location_id IS NOT NULL THEN
        SELECT available_quantity INTO balance FROM inventory_availability
        WHERE organization_id = NEW.organization_id AND item_revision_id = NEW.item_revision_id
          AND location_id = NEW.from_location_id;
        IF coalesce(balance, 0) < 0 THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'inventory_insufficient_stock',
                MESSAGE = 'Movement would consume unavailable stock',
                DETAIL = format('movement_id=%s organization_id=%s item_revision_id=%s from_location_id=%s quantity=%s available_after_movement=%s',
                                NEW.movement_id, NEW.organization_id, NEW.item_revision_id, NEW.from_location_id, NEW.quantity, balance),
                HINT = 'Reduce the quantity or post the missing stock before retrying.';
        END IF;
    END IF;
    RETURN NEW;
END $$;
DROP TRIGGER check_movement_stock ON inventory_movement_lines;
CREATE TRIGGER check_movement_stock AFTER INSERT ON inventory_movement_lines
FOR EACH ROW EXECUTE FUNCTION validate_movement_balance();
