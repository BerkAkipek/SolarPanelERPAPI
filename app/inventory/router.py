"""HTTP interface for step 2: physical inventory ledger."""
from typing import Annotated

from fastapi import APIRouter, Header

from app.dependencies import DatabaseSession, OrganizationID, PathID
from app.inventory import service
from app.inventory.schemas import (
    AvailabilityRead,
    MovementCreate,
    MovementRead,
    OnHandRead,
    ReservationCreate,
    ReservationRead,
)

router = APIRouter(prefix="/inventory", tags=["Inventory ledger"])


@router.post("/movements", response_model=MovementRead, status_code=201, summary="Post double-entry inventory movement")
def post_movement(
    payload: MovementCreate, session: DatabaseSession, organization_id: OrganizationID,
    idempotency_key: Annotated[str | None, Header(min_length=1, max_length=100, pattern=r"^\S+$")] = None,
):
    """Posts an append-only double-entry physical inventory movement with one or more lines.

    - Movement types: RECEIPT, SHIPMENT, TRANSFER, ADJUSTMENT_IN, ADJUSTMENT_OUT, PRODUCTION_CONSUMPTION, PRODUCTION_OUTPUT.
    - Database triggers enforce that source location balances never drop below zero.
    - Safe retries via Idempotency-Key header.
    """
    return service.post_movement(session, organization_id, payload, idempotency_key)


@router.get("/items/{revision_id}/on-hand", response_model=OnHandRead, summary="Get physical on-hand stock")
def get_on_hand(revision_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Calculates real-time physical on-hand inventory across all locations for an item revision.

    - Formula: Incoming - Outgoing across append-only movement lines.
    - Includes warehouse stock, production bins, and quarantine/damaged stock.
    """
    return service.on_hand(session, organization_id, revision_id)


@router.get("/items/{revision_id}/availability", response_model=AvailabilityRead, summary="Get available stock")
def get_availability(revision_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Calculates available-to-promise inventory accounting for active reservations.

    - Formula: On Hand - Active Reservations.
    - All three totals and the location breakdown cover the same eligible locations.
    - Reservable Locations Invariant: Only active locations of type WAREHOUSE, BIN, and PRODUCTION count.
    - Quarantine (QUARANTINE), damaged, and transit locations are strictly excluded.
    - Use /on-hand for physical stock across all locations, including excluded stock.
    """
    return service.availability(session, organization_id, revision_id)


@router.post("/reservations", response_model=ReservationRead, status_code=201, summary="Create stock reservation")
def post_reservation(payload: ReservationCreate, session: DatabaseSession, organization_id: OrganizationID):
    """Creates an active stock reservation against available inventory.

    - Option A Traceability: Must reference either a sales_order_line_id or production_order_material_id.
    - Rejects if quantity exceeds currently available stock at the location.
    """
    return service.create_reservation(session, organization_id, payload)


@router.get("/reservations/{reservation_id}", response_model=ReservationRead, summary="Get reservation details")
def get_reservation(reservation_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Retrieves reservation details, current status (ACTIVE, RELEASED, or CONSUMED), and ownership."""
    return service.get_reservation(session, organization_id, reservation_id)


@router.delete("/reservations/{reservation_id}", response_model=ReservationRead, summary="Release reservation")
@router.post("/reservations/{reservation_id}/release", response_model=ReservationRead, summary="Release reservation")
def delete_reservation(reservation_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Explicitly releases an active stock reservation (status -> RELEASED).

    - Restores available stock to the location.
    - Row is preserved in database for audit history; never physically deleted.
    """
    return service.release_reservation(session, organization_id, reservation_id)


@router.post("/reservations/{reservation_id}/consume", response_model=ReservationRead, summary="Consume reservation")
def consume_reservation(reservation_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Atomically consumes an active reservation and posts its physical stock issue.

    - Sales allocations post SHIPMENT; material allocations post PRODUCTION_CONSUMPTION.
    - Retrying a consumed reservation returns its existing state without another issue.
    - Production order completion owns its own atomic material and output workflow.
    - Terminal state; consumed reservations cannot be released.
    """
    return service.consume_reservation(session, organization_id, reservation_id)
