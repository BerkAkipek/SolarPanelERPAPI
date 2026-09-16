"""Translate known domain/database failures without exposing SQL or credentials."""
import logging

from fastapi import Request
from fastapi.responses import JSONResponse
from sqlalchemy.exc import DBAPIError

logger = logging.getLogger(__name__)


class DomainError(Exception):
    def __init__(self, status: int, code: str, message: str):
        self.status = status
        self.code = code
        self.message = message
        super().__init__(message)


def domain_error_handler(request: Request, error: DomainError):
    return JSONResponse(
        status_code=error.status,
        content={"detail": {"code": error.code, "message": error.message}},
    )


# Database constraints own these invariants; this mapping gives callers stable API errors.
CONSTRAINT_ERRORS = {
    "inventory_insufficient_stock": (409, "insufficient_stock", "Movement would consume more stock than available at the source location."),
    "inventory_movement_direction": (422, "invalid_movement_direction", "Receipts and ADJUSTMENT_IN require only a destination; ADJUSTMENT_OUT only a source; TRANSFER requires both."),
    "uq_movement_idempotency": (409, "idempotency_conflict", "This Idempotency-Key was already used for another movement."),
    "items_organization_id_sku_key": (409, "duplicate_sku", "SKU already exists in this organization."),
    "item_revisions_item_id_revision_code_key": (409, "duplicate_revision", "Revision code already exists for this item."),
    "boms_product_revision_id_version_key": (409, "duplicate_bom_version", "BOM version already exists for this product revision."),
    "bom_lines_bom_id_line_no_key": (409, "duplicate_bom_line", "Line number already exists in this BOM."),
    "uq_bom_component": (409, "duplicate_component", "Component revision already exists in this BOM."),
    "bom_locked": (409, "bom_locked", "This BOM is locked. Create a new version to change its recipe."),
    "bom_finished_good_only": (422, "finished_good_required", "Only FINISHED_GOOD items can have a BOM."),
    "bom_self_reference": (422, "bom_self_reference", "A BOM cannot contain its own product revision."),
    "bom_empty": (409, "bom_empty", "Add at least one component before activating the BOM."),
    "bom_start_draft": (422, "bom_start_draft", "Create a DRAFT BOM, add its lines, then activate it."),
    "bom_product_revision_missing": (404, "revision_not_found", "Product revision was not found."),
    "stock_reservations_check": (422, "invalid_reservation_owner", "Reservation requires exactly one owner: sales_order_line_id or production_order_material_id."),
    "stock_reservations_organization_id_sales_order_line_id_ite_fkey": (422, "sales_order_line_not_found", "Sales order line was not found or does not match the item revision."),
    "stock_reservations_organization_id_sales_order_line_id_item_r_fkey": (422, "sales_order_line_not_found", "Sales order line was not found or does not match the item revision."),
    "stock_reservations_organization_id_production_order_materia_fkey": (422, "production_order_material_not_found", "Production order material was not found or does not match the item revision."),
    "stock_reservations_organization_id_production_order_material_i_fkey": (422, "production_order_material_not_found", "Production order material was not found or does not match the item revision."),
    "stock_reservations_organization_id_location_id_fkey": (404, "location_not_found", "Inventory location was not found in this organization."),
    "stock_reservations_organization_id_item_revision_id_fkey": (404, "revision_not_found", "Item revision was not found in this organization."),
    "sales_orders_organization_id_order_number_key": (409, "duplicate_order_number", "Order number already exists in this organization."),
    "sales_order_lines_sales_order_id_line_no_key": (409, "duplicate_line_no", "Line number already exists in this sales order."),
    "sales_orders_customer_id_fkey": (404, "customer_not_found", "Customer was not found in this organization."),
    "sales_orders_organization_id_customer_id_fkey": (404, "customer_not_found", "Customer was not found in this organization."),
    "sales_order_lines_organization_id_item_revision_id_fkey": (404, "revision_not_found", "Item revision was not found in this organization."),
    "production_orders_organization_id_production_order_number_key": (409, "duplicate_order_number", "Production order number already exists in this organization."),
    "production_orders_status_check": (422, "invalid_production_status", "Production order status is invalid."),
    "production_orders_organization_id_product_revision_id_fkey": (404, "revision_not_found", "Product revision was not found in this organization."),
    "production_orders_organization_id_bom_id_product_revision_id_fkey": (422, "bom_not_found", "BOM was not found or does not match the product revision."),
    "production_orders_organization_id_source_sales_order_line_id_pro_fkey": (422, "sales_order_line_not_found", "Sales order line was not found or does not match the product revision."),
    "production_order_materials_production_order_id_bom_line_id_key": (409, "duplicate_material_line", "Material line already exists for this production order."),
}



def database_error_handler(request: Request, error: DBAPIError):
    original = error.orig
    sqlstate = getattr(original, "sqlstate", None)
    diagnostic = getattr(original, "diag", None)
    constraint = getattr(diagnostic, "constraint_name", None)
    result = CONSTRAINT_ERRORS.get(constraint)
    if result is None and constraint:
        if constraint.startswith("stock_reservations_organization_id_sales_order_line"):
            result = (422, "sales_order_line_not_found", "Sales order line was not found or does not match the item revision.")
        elif constraint.startswith("stock_reservations_organization_id_production_order"):
            result = (422, "production_order_material_not_found", "Production order material was not found or does not match the item revision.")
    if result is None and diagnostic is not None:
        msg = getattr(diagnostic, "message_primary", "") or ""
        if "Insufficient available stock" in msg:
            result = (409, "insufficient_stock", "Reservation quantity exceeds available stock at this location.")
        elif "Stock location is not reservable" in msg:

            result = (422, "location_not_reservable", "The selected stock location is not eligible for reservations.")
        elif "Reservation exceeds demand" in msg:
            result = (409, "reservation_exceeds_demand", "Reservation quantity exceeds document demand.")
        elif "terminal reservation" in msg:
            result = (409, "reservation_terminal", "A terminal reservation cannot change status.")
        elif "Reservation allocation is immutable" in msg:
            result = (422, "reservation_immutable", "Reservation allocation is immutable.")
        elif "must start ACTIVE" in msg:
            result = (422, "reservation_invalid_status", "A reservation must start ACTIVE.")
    if result is None:
        result = {
            "23505": (409, "duplicate_record", "A record with these identifiers already exists."),
            "23503": (409, "reference_conflict", "A referenced record is missing or has changed."),
            "23514": (422, "invalid_record", "The requested change violates a business rule."),
            "40001": (409, "transaction_conflict", "A concurrent change prevented this operation. Retry the request."),
            "40P01": (409, "transaction_conflict", "A concurrent change prevented this operation. Retry the request."),
        }.get(sqlstate)

    if result is None and (error.connection_invalidated or (sqlstate or "").startswith("08")):
        result = (503, "database_unavailable", "The database is temporarily unavailable.")
    if result is None:
        result = (500, "database_error", "The operation could not be completed.")
    logger.log(
        logging.ERROR if result[0] >= 500 else logging.WARNING,
        "Database operation failed: method=%s path=%s sqlstate=%s constraint=%s",
        request.method, request.url.path, sqlstate, constraint,
    )
    return JSONResponse(
        status_code=result[0], content={"detail": {"code": result[1], "message": result[2]}},
    )
