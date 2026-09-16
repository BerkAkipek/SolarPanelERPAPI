-- Support explicit DRAFT status on production orders for clean creation before release
ALTER TABLE production_orders DROP CONSTRAINT production_orders_status_check;
ALTER TABLE production_orders ADD CONSTRAINT production_orders_status_check
CHECK (status IN ('DRAFT', 'PLANNED', 'APPROVED', 'RELEASED', 'IN_PRODUCTION', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED'));

ALTER TABLE production_orders ALTER COLUMN status SET DEFAULT 'DRAFT';
