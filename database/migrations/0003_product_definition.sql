-- Canonical product vocabulary for the first API capability.
-- Existing recipe identities, quantities, and production references are preserved.
ALTER TABLE items DROP CONSTRAINT items_item_type_check;
UPDATE items SET item_type = 'FINISHED_GOOD' WHERE item_type = 'PRODUCT';
ALTER TABLE items ADD CONSTRAINT items_item_type_check
    CHECK (item_type IN ('MATERIAL', 'COMPONENT', 'FINISHED_GOOD'));

ALTER TABLE boms DROP CONSTRAINT boms_status_check;
ALTER TABLE boms DISABLE TRIGGER protect_bom_header;
UPDATE boms SET status = 'ACTIVE' WHERE status = 'APPROVED';
ALTER TABLE boms ENABLE TRIGGER protect_bom_header;
ALTER TABLE boms ADD CONSTRAINT boms_status_check
    CHECK (status IN ('DRAFT', 'ACTIVE', 'OBSOLETE'));

-- Fail rather than silently reinterpret any incompatible existing recipe.
DO $$
BEGIN
    IF EXISTS (
        SELECT 1 FROM boms b JOIN item_revisions r ON r.id = b.product_revision_id
        JOIN items i ON i.id = r.item_id WHERE i.item_type <> 'FINISHED_GOOD'
    ) THEN
        RAISE EXCEPTION 'Existing BOM belongs to a non-finished-good item; correct it before upgrading';
    END IF;
    IF EXISTS (
        SELECT 1 FROM bom_lines l JOIN boms b ON b.id = l.bom_id
        WHERE l.component_revision_id = b.product_revision_id
    ) THEN
        RAISE EXCEPTION 'Existing BOM contains its own product revision; correct it before upgrading';
    END IF;
    IF EXISTS (
        SELECT 1 FROM boms b WHERE b.status = 'ACTIVE'
        AND NOT EXISTS (SELECT 1 FROM bom_lines l WHERE l.bom_id = b.id)
    ) THEN
        RAISE EXCEPTION 'Existing active BOM has no lines; correct it before upgrading';
    END IF;
END $$;

ALTER TABLE bom_lines ADD CONSTRAINT uq_bom_component UNIQUE (bom_id, component_revision_id);

CREATE FUNCTION validate_bom_product() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE kind text;
BEGIN
    -- This lock also serializes a concurrent change to the item's type.
    SELECT i.item_type INTO kind FROM items i
    JOIN item_revisions r ON r.item_id = i.id AND r.organization_id = i.organization_id
    WHERE r.id = NEW.product_revision_id AND r.organization_id = NEW.organization_id
    FOR SHARE OF i;
    IF NOT FOUND THEN
        RAISE EXCEPTION USING ERRCODE = '23503', CONSTRAINT = 'bom_product_revision_missing',
            MESSAGE = 'Product revision does not exist in this organization';
    END IF;
    IF kind <> 'FINISHED_GOOD' THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'bom_finished_good_only',
            MESSAGE = 'Only FINISHED_GOOD items can have a BOM';
    END IF;
    IF EXISTS (
        SELECT 1 FROM bom_lines WHERE bom_id = NEW.id
        AND component_revision_id = NEW.product_revision_id
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'bom_self_reference',
            MESSAGE = 'A BOM cannot contain its own product revision';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER validate_bom_product BEFORE INSERT OR UPDATE ON boms
FOR EACH ROW EXECUTE FUNCTION validate_bom_product();

CREATE FUNCTION protect_manufactured_item_type() RETURNS trigger LANGUAGE plpgsql AS $$
BEGIN
    IF NEW.item_type <> 'FINISHED_GOOD' AND EXISTS (
        SELECT 1 FROM boms b JOIN item_revisions r ON r.id = b.product_revision_id
        WHERE r.item_id = OLD.id
    ) THEN
        RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'bom_finished_good_only',
            MESSAGE = 'An item with a BOM must remain a FINISHED_GOOD';
    END IF;
    RETURN NEW;
END $$;
CREATE TRIGGER protect_manufactured_item_type BEFORE UPDATE OF item_type ON items
FOR EACH ROW EXECUTE FUNCTION protect_manufactured_item_type();

CREATE OR REPLACE FUNCTION protect_bom() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE parent_status text; product_id bigint;
BEGIN
    IF TG_TABLE_NAME = 'bom_lines' THEN
        IF TG_OP = 'UPDATE' AND (NEW.bom_id, NEW.organization_id) IS DISTINCT FROM (OLD.bom_id, OLD.organization_id) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'bom_line_parent_immutable',
                MESSAGE = 'BOM lines cannot move between BOMs';
        END IF;
        SELECT status, product_revision_id INTO parent_status, product_id FROM boms
        WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.bom_id ELSE NEW.bom_id END
        FOR UPDATE;
        IF parent_status <> 'DRAFT' THEN
            RAISE EXCEPTION USING ERRCODE = '55000', CONSTRAINT = 'bom_locked',
                MESSAGE = 'Only draft BOM lines can change';
        END IF;
        IF TG_OP <> 'DELETE' AND NEW.component_revision_id = product_id THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'bom_self_reference',
                MESSAGE = 'A BOM cannot contain its own product revision';
        END IF;
    ELSE
        IF TG_OP = 'INSERT' THEN
            IF NEW.status <> 'DRAFT' THEN
                RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'bom_start_draft',
                    MESSAGE = 'A BOM must start DRAFT; add lines and activate it';
            END IF;
        ELSIF OLD.status <> 'DRAFT' THEN
            IF TG_OP = 'DELETE' THEN
                RAISE EXCEPTION USING ERRCODE = '55000', CONSTRAINT = 'bom_locked',
                    MESSAGE = 'Active BOM history cannot be deleted';
            END IF;
            IF (NEW.id, NEW.organization_id, NEW.product_revision_id, NEW.version, NEW.output_quantity, NEW.created_at)
               IS DISTINCT FROM (OLD.id, OLD.organization_id, OLD.product_revision_id, OLD.version, OLD.output_quantity, OLD.created_at)
               OR NEW.status NOT IN (OLD.status, 'OBSOLETE') THEN
                RAISE EXCEPTION USING ERRCODE = '55000', CONSTRAINT = 'bom_locked',
                    MESSAGE = 'Active BOM recipe is immutable; create a new version';
            END IF;
        END IF;
        IF TG_OP <> 'DELETE' AND NEW.status = 'ACTIVE' AND NOT EXISTS (
            SELECT 1 FROM bom_lines WHERE bom_id = NEW.id
        ) THEN
            RAISE EXCEPTION USING ERRCODE = '23514', CONSTRAINT = 'bom_empty',
                MESSAGE = 'A BOM needs at least one component before activation';
        END IF;
    END IF;
    IF TG_OP = 'DELETE' THEN RETURN OLD; END IF;
    RETURN NEW;
END $$;
DROP TRIGGER protect_bom_header ON boms;
CREATE TRIGGER protect_bom_header BEFORE INSERT OR UPDATE OR DELETE ON boms
FOR EACH ROW EXECUTE FUNCTION protect_bom();

-- Production must continue to recognize active recipes after the vocabulary migration.
CREATE OR REPLACE FUNCTION validate_production() RETURNS trigger LANGUAGE plpgsql AS $$
DECLARE recipe_status text; calculated numeric;
BEGIN
    IF TG_TABLE_NAME = 'production_orders' THEN
        SELECT status INTO recipe_status FROM boms WHERE id = NEW.bom_id FOR SHARE;
        IF recipe_status <> 'ACTIVE' THEN RAISE EXCEPTION 'Production requires an active exact BOM'; END IF;
    ELSE
        SELECT round(p.quantity * l.quantity / b.output_quantity, 6) INTO calculated
        FROM production_orders p JOIN boms b ON b.id = p.bom_id
        JOIN bom_lines l ON l.bom_id = b.id
        WHERE p.id = NEW.production_order_id AND l.id = NEW.bom_line_id;
        IF NEW.required_quantity IS DISTINCT FROM calculated THEN
            RAISE EXCEPTION 'Material requirement must equal production quantity * BOM line quantity / BOM output quantity';
        END IF;
    END IF;
    RETURN NEW;
END $$;
