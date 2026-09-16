"""HTTP interface for sales orders."""
from fastapi import APIRouter

from app.dependencies import DatabaseSession, OrganizationID, PathID
from app.production.schemas import ProductionOrderCreate, ProductionOrderRead
from app.sales import service
from app.sales.schemas import (
    FulfillmentAnalysisRead,
    SalesOrderCreate,
    SalesOrderFeasibilityRead,
    SalesOrderLineCreate,
    SalesOrderLineRead,
    SalesOrderRead,
)

router = APIRouter(prefix="/sales-orders", tags=["Sales orders"])


@router.post("", response_model=SalesOrderRead, status_code=201, summary="Create sales order")
def post_sales_order(payload: SalesOrderCreate, session: DatabaseSession, organization_id: OrganizationID):
    """Creates a new customer sales order in DRAFT status.

    - Order numbers must be unique per organization.
    - Customer partner must exist and be active.
    """
    return service.create_sales_order(session, organization_id, payload)


@router.get("/{order_id}", response_model=SalesOrderRead, summary="Get sales order details")
def get_sales_order(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Retrieves sales order details, customer header, status, and lines."""
    return service.read_sales_order(session, organization_id, order_id)


@router.get("/{order_id}/fulfillment-analysis", response_model=FulfillmentAnalysisRead, summary="Analyze order fulfillment")
def get_fulfillment_analysis(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Evaluates whether the order can be fulfilled from current stock or requires manufacturing.

    - Read-only: does not modify inventory, reserve stock, or create production orders.
    - Computes for each line: ordered_quantity, available_quantity, ship_from_stock, and production_required.
    - Returns overall flag: can_fulfill_entirely_from_stock.
    """
    return service.analyze_sales_order_fulfillment(session, organization_id, order_id)


@router.get("/{order_id}/material-feasibility", response_model=SalesOrderFeasibilityRead, summary="Analyze material feasibility for order")
def get_material_feasibility(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Evaluates component raw material availability to manufacture panels required by this order.

    - Combines fulfillment analysis with BOM explosion.
    - Identifies exact required quantities, reservable component stock, and shortages.
    - Excludes quarantine and damaged stock from available components.
    - Returns can_produce and can_fulfill_all flags.
    """
    return service.analyze_sales_order_material_feasibility(session, organization_id, order_id)


@router.post("/{order_id}/production-orders", response_model=ProductionOrderRead, status_code=201, summary="Create production order for sales order")
def post_sales_order_production_order(
    order_id: PathID,
    payload: ProductionOrderCreate,
    session: DatabaseSession,
    organization_id: OrganizationID,
):
    """Creates a manufacturing order in DRAFT status tied directly to a sales order line.

    - Snapshots the active BOM recipe and material requirements.
    - Enables end-to-end traceability from customer order to manufactured module.
    """
    from app.production.service import create_sales_order_production_order
    return create_sales_order_production_order(session, organization_id, order_id, payload)


@router.post("/{order_id}/lines", response_model=SalesOrderLineRead, status_code=201, summary="Add line to draft sales order")
def post_sales_order_line(
    order_id: PathID, payload: SalesOrderLineCreate, session: DatabaseSession, organization_id: OrganizationID
):
    """Appends an order line with revision, quantity, and unit price to a DRAFT sales order."""
    return service.add_sales_order_line(session, organization_id, order_id, payload)


@router.post("/{order_id}/confirm", response_model=SalesOrderRead, summary="Confirm sales order")
def post_confirm_sales_order(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Explicit business operation to confirm a DRAFT sales order (status -> CONFIRMED).

    - Enforces order invariants: order must not be empty, revisions must be ACTIVE, cancelled orders cannot be confirmed.
    - Idempotent: repeated confirmation of an already confirmed order succeeds safely.
    """
    return service.confirm_sales_order(session, organization_id, order_id)


@router.post("/{order_id}/cancel", response_model=SalesOrderRead, summary="Cancel sales order")
def post_cancel_sales_order(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Cancels a sales order (status -> CANCELLED).

    - Prevents cancelling shipped orders.
    - Requires active stock reservations to be released before cancelling.
    """
    return service.cancel_sales_order(session, organization_id, order_id)

