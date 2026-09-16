-- Tighten existing operations without changing the V1 tables or rewriting history.
CREATE FUNCTION validate_reservation_creation() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.status <> 'ACTIVE' THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'A reservation must start ACTIVE',
            DETAIL = format('reservation_id=%s organization_id=%s status=%s', NEW.id, NEW.organization_id, NEW.status),
            HINT = 'Reserve stock first, then consume or release the reservation.';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER validate_reservation_creation BEFORE INSERT ON stock_reservations
FOR EACH ROW EXECUTE FUNCTION validate_reservation_creation();

CREATE OR REPLACE FUNCTION validate_stock() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE balance numeric; reserved_total numeric; demand numeric; inputs text;
BEGIN
    inputs := format('organization_id=%s item_revision_id=%s record_id=%s quantity=%s',
                     NEW.organization_id, NEW.item_revision_id, NEW.id, NEW.quantity);
    IF TG_TABLE_NAME = 'stock_reservations' THEN
        inputs := inputs || format(' location_id=%s sales_order_line_id=%s production_order_material_id=%s',
                                   NEW.location_id, NEW.sales_order_line_id, NEW.production_order_material_id);
        IF TG_OP = 'UPDATE' THEN
            IF (NEW.organization_id, NEW.item_revision_id, NEW.location_id,
                NEW.sales_order_line_id, NEW.production_order_material_id, NEW.quantity, NEW.created_at)
               IS DISTINCT FROM
               (OLD.organization_id, OLD.item_revision_id, OLD.location_id,
                OLD.sales_order_line_id, OLD.production_order_material_id, OLD.quantity, OLD.created_at) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'Reservation allocation is immutable; release and create a new reservation',
                    DETAIL = inputs;
            END IF;
            IF OLD.status <> 'ACTIVE' AND NEW.status <> OLD.status THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'A terminal reservation cannot change status',
                    DETAIL = inputs || format(' old_status=%s new_status=%s', OLD.status, NEW.status);
            END IF;
        END IF;
        IF NEW.status = 'ACTIVE' THEN
            IF NOT EXISTS (
                SELECT 1 FROM inventory_locations WHERE id = NEW.location_id
                AND organization_id = NEW.organization_id
                AND is_active AND location_type IN ('WAREHOUSE', 'BIN', 'PRODUCTION')
            ) THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'Stock location is not reservable', DETAIL = inputs;
            END IF;
            SELECT available_quantity INTO balance FROM inventory_availability
            WHERE organization_id = NEW.organization_id AND item_revision_id = NEW.item_revision_id
              AND location_id = NEW.location_id;
            IF coalesce(balance, 0) < 0 THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'Insufficient available stock',
                    DETAIL = inputs || format(' available_after_reservation=%s', balance),
                    HINT = 'Recheck availability and reserve only the available quantity.';
            END IF;
            IF NEW.sales_order_line_id IS NOT NULL THEN
                SELECT quantity INTO demand FROM sales_order_lines WHERE id = NEW.sales_order_line_id;
                SELECT sum(quantity) INTO reserved_total FROM stock_reservations
                WHERE sales_order_line_id = NEW.sales_order_line_id AND status IN ('ACTIVE', 'CONSUMED');
            ELSE
                SELECT required_quantity INTO demand FROM production_order_materials
                WHERE id = NEW.production_order_material_id;
                SELECT sum(quantity) INTO reserved_total FROM stock_reservations
                WHERE production_order_material_id = NEW.production_order_material_id AND status IN ('ACTIVE', 'CONSUMED');
            END IF;
            IF reserved_total > demand THEN
                RAISE EXCEPTION USING ERRCODE = '23514',
                    MESSAGE = 'Reservation exceeds demand',
                    DETAIL = inputs || format(' allocated_quantity=%s demand_quantity=%s', reserved_total, demand);
            END IF;
        END IF;
    ELSIF NEW.from_location_id IS NOT NULL THEN
        SELECT available_quantity INTO balance FROM inventory_availability
        WHERE organization_id = NEW.organization_id AND item_revision_id = NEW.item_revision_id
          AND location_id = NEW.from_location_id;
        IF coalesce(balance, 0) < 0 THEN
            RAISE EXCEPTION USING ERRCODE = '23514',
                MESSAGE = 'Movement would consume unavailable stock',
                DETAIL = inputs || format(' from_location_id=%s available_after_movement=%s', NEW.from_location_id, balance),
                HINT = 'Consume the matching reservation and post the movement in the same transaction.';
        END IF;
    END IF;
    RETURN NEW;
END $$;

CREATE FUNCTION validate_sales_demand() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE allocated numeric;
BEGIN
    SELECT coalesce(sum(quantity), 0) INTO allocated FROM stock_reservations
    WHERE sales_order_line_id = OLD.id AND status IN ('ACTIVE', 'CONSUMED');
    IF NEW.quantity < allocated THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'Sales quantity cannot be less than allocated stock',
            DETAIL = format('sales_order_line_id=%s organization_id=%s quantity=%s allocated_quantity=%s',
                            OLD.id, OLD.organization_id, NEW.quantity, allocated),
            HINT = 'Release unused reservations before reducing demand; consumed quantities remain allocated.';
    END IF;
    RETURN NEW;
END $$;
-- Shares the existing stock lock so a concurrent reservation cannot slip past the check.
CREATE TRIGGER lock_sales_demand BEFORE UPDATE OF quantity ON sales_order_lines
FOR EACH ROW EXECUTE FUNCTION lock_stock();
CREATE TRIGGER validate_sales_demand BEFORE UPDATE OF quantity ON sales_order_lines
FOR EACH ROW EXECUTE FUNCTION validate_sales_demand();

CREATE FUNCTION validate_movement_direction() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kind text;
BEGIN
    SELECT movement_type INTO kind FROM inventory_movements
    WHERE id = NEW.movement_id AND organization_id = NEW.organization_id;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23503',
            MESSAGE = 'Inventory movement does not exist in this organization',
            DETAIL = format('movement_id=%s organization_id=%s', NEW.movement_id, NEW.organization_id);
    END IF;
    IF (kind IN ('RECEIPT', 'PRODUCTION_OUTPUT') AND
        (NEW.from_location_id IS NOT NULL OR NEW.to_location_id IS NULL))
       OR (kind IN ('SHIPMENT', 'PRODUCTION_CONSUMPTION') AND
           (NEW.from_location_id IS NULL OR NEW.to_location_id IS NOT NULL))
       OR (kind = 'TRANSFER' AND
           (NEW.from_location_id IS NULL OR NEW.to_location_id IS NULL)) THEN
        RAISE EXCEPTION USING ERRCODE = '23514',
            MESSAGE = 'Movement locations do not match the movement type',
            DETAIL = format('movement_id=%s movement_type=%s from_location_id=%s to_location_id=%s',
                            NEW.movement_id, kind, NEW.from_location_id, NEW.to_location_id),
            HINT = 'Receipts need only a destination, issues only a source, and transfers both.';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER validate_movement_direction BEFORE INSERT ON inventory_movement_lines
FOR EACH ROW EXECUTE FUNCTION validate_movement_direction();
