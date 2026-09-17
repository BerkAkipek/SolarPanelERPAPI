"""HTTP interface for production, BOM explosion, and production orders."""
from decimal import Decimal
from typing import Annotated

from fastapi import APIRouter, Query

from app.dependencies import DatabaseSession, OrganizationID, PathID
from app.errors import DomainError
from app.production import service
from app.production.schemas import (
    BOMExplosionRead,
    MaterialFeasibilityRead,
    ProductionOrderComplete,
    ProductionOrderCreate,
    ProductionOrderRead,
    ProductionOrderRelease,
)
from app.schemas import Quantity

router = APIRouter(prefix="/production", tags=["Production"])
production_orders_router = APIRouter(prefix="/production-orders", tags=["Production orders"])


# ---------------------------------------------------------------------------
# BOM explosion & feasibility
# ---------------------------------------------------------------------------


@router.get("/boms/{bom_id}/explode", response_model=BOMExplosionRead, summary="Explode BOM recipe")
def get_bom_explosion(
    bom_id: PathID,
    quantity: Annotated[Decimal, Query(gt=0, description="Target production quantity")],
    session: DatabaseSession,
    organization_id: OrganizationID,
):
    """Pure domain function that explodes a BOM recipe for a target production quantity.

    - Formula: round(production_quantity * component_quantity / bom_output_quantity, 6).
    - Read-only: zero database mutations.
    """
    return service.explode_bom_recipe(session, organization_id, bom_id, quantity)


@router.get("/boms/{bom_id}/feasibility", response_model=MaterialFeasibilityRead, summary="Analyze BOM material feasibility")
def get_bom_feasibility(
    bom_id: PathID,
    session: DatabaseSession,
    organization_id: OrganizationID,
    quantity: Annotated[Decimal | None, Query(gt=0, max_digits=18, decimal_places=6, allow_inf_nan=False, description="Target production quantity")] = None,
    production_required: Annotated[Decimal | None, Query(gt=0, max_digits=18, decimal_places=6, allow_inf_nan=False, description="Target production quantity (alias)")] = None,
):
    """Evaluates whether sufficient raw components exist in reservable stock to manufacture the requested quantity.

    - Checks only active WAREHOUSE, BIN, and PRODUCTION locations.
    - Excludes quarantine, damaged, and transit inventory.
    - Reports exact shortage quantities per component and overall can_produce flag.
    """
    if quantity is not None and production_required is not None and quantity != production_required:
        raise DomainError(422, "conflicting_quantity", "quantity and production_required must agree when both are supplied.")
    target_quantity = quantity if quantity is not None else production_required
    if target_quantity is None:
        raise DomainError(422, "missing_quantity", "Either 'quantity' or 'production_required' query parameter must be provided.")
    return service.check_material_feasibility(session, organization_id, bom_id, target_quantity)


# ---------------------------------------------------------------------------
# Production orders (accessible via both /production/orders and /production-orders)
# ---------------------------------------------------------------------------


def _create_order(payload: ProductionOrderCreate, session: DatabaseSession, organization_id: OrganizationID):
    return service.create_production_order(session, organization_id, payload)


def _get_order(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    return service.read_production_order(session, organization_id, order_id)


def _release_order(
    order_id: PathID,
    session: DatabaseSession,
    organization_id: OrganizationID,
    payload: ProductionOrderRelease | None = None,
):
    return service.release_production_order(session, organization_id, order_id, payload)


def _start_order(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    return service.start_production_order(session, organization_id, order_id)


def _complete_order(
    order_id: PathID,
    session: DatabaseSession,
    organization_id: OrganizationID,
    payload: ProductionOrderComplete | None = None,
):
    return service.complete_production_order(session, organization_id, order_id, payload)


def _cancel_order(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    return service.cancel_production_order(session, organization_id, order_id)


# /production/orders endpoints
@router.post("/orders", response_model=ProductionOrderRead, status_code=201, summary="Create production order")
def post_production_order(payload: ProductionOrderCreate, session: DatabaseSession, organization_id: OrganizationID):
    """Creates a new production order in DRAFT status.

    - Freezes the active BOM version and calculates material requirements.
    - Does not reserve stock; keeps creation and commitment separate.
    """
    return _create_order(payload, session, organization_id)


@router.get("/orders/{order_id}", response_model=ProductionOrderRead, summary="Get production order details")
def get_production_order(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Retrieves production order details, status, and frozen material requirements."""
    return _get_order(order_id, session, organization_id)


@router.post("/orders/{order_id}/release", response_model=ProductionOrderRead, summary="Release production order and reserve materials")
def post_release_production_order(
    order_id: PathID,
    session: DatabaseSession,
    organization_id: OrganizationID,
    payload: ProductionOrderRelease | None = None,
):
    """Releases a DRAFT production order (status -> RELEASED) and reserves required component materials.

    - Option A Ownership: reservations reference production_order_material_id.
    - All-or-Nothing: fails with 409 Conflict (insufficient_stock) if any component is unavailable.
    """
    return _release_order(order_id, session, organization_id, payload)


@router.post("/orders/{order_id}/start", response_model=ProductionOrderRead, summary="Start production order")
def post_start_production_order(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Transitions a RELEASED production order to IN_PROGRESS.

    - Idempotent; rejects unreleased or cancelled orders.
    """
    return _start_order(order_id, session, organization_id)


@router.post("/orders/{order_id}/complete", response_model=ProductionOrderRead, summary="Atomically complete production order")
def post_complete_production_order(
    order_id: PathID,
    session: DatabaseSession,
    organization_id: OrganizationID,
    payload: ProductionOrderComplete | None = None,
):
    """Atomically completes production execution within a single database transaction:

    1. Consumes active material reservations (status -> CONSUMED).
    2. Posts raw material consumption movements (PRODUCTION_CONSUMPTION).
    3. Posts finished goods output movement (PRODUCTION_OUTPUT) into destination warehouse.
    4. Marks order COMPLETED.
    - All-or-nothing: if physical balances are insufficient, entire transaction rolls back cleanly.
    - Idempotent: repeated completion of an already completed order returns safely.
    """
    return _complete_order(order_id, session, organization_id, payload)


@router.post("/orders/{order_id}/cancel", response_model=ProductionOrderRead, summary="Cancel production order")
def post_cancel_production_order(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Cancels a production order (status -> CANCELLED) and releases active component reservations."""
    return _cancel_order(order_id, session, organization_id)


# /production-orders direct endpoints
@production_orders_router.post("", response_model=ProductionOrderRead, status_code=201, summary="Create production order")
def post_direct_production_order(payload: ProductionOrderCreate, session: DatabaseSession, organization_id: OrganizationID):
    """Creates a new production order in DRAFT status with frozen material requirements."""
    return _create_order(payload, session, organization_id)


@production_orders_router.get("/{order_id}", response_model=ProductionOrderRead, summary="Get production order details")
def get_direct_production_order(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Retrieves production order details, current status, and component requirements."""
    return _get_order(order_id, session, organization_id)


@production_orders_router.post("/{order_id}/release", response_model=ProductionOrderRead, summary="Release production order and reserve materials")
def post_direct_release_production_order(
    order_id: PathID,
    session: DatabaseSession,
    organization_id: OrganizationID,
    payload: ProductionOrderRelease | None = None,
):
    """Releases a DRAFT production order and reserves all required component materials."""
    return _release_order(order_id, session, organization_id, payload)


@production_orders_router.post("/{order_id}/start", response_model=ProductionOrderRead, summary="Start production order")
def post_direct_start_production_order(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Transitions a RELEASED production order to IN_PROGRESS."""
    return _start_order(order_id, session, organization_id)


@production_orders_router.post("/{order_id}/complete", response_model=ProductionOrderRead, summary="Atomically complete production order")
def post_direct_complete_production_order(
    order_id: PathID,
    session: DatabaseSession,
    organization_id: OrganizationID,
    payload: ProductionOrderComplete | None = None,
):
    """Atomically completes production execution within a single database transaction."""
    return _complete_order(order_id, session, organization_id, payload)


@production_orders_router.post("/{order_id}/cancel", response_model=ProductionOrderRead, summary="Cancel production order")
def post_direct_cancel_production_order(order_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Cancels a production order and releases active component reservations."""
    return _cancel_order(order_id, session, organization_id)
