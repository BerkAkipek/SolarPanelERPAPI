"""Append-only movement posting and physical stock queries."""
from datetime import timezone
from decimal import Decimal
import hashlib
import json
import logging
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.errors import DomainError
from app.inventory.models import (
    InventoryAvailability,
    InventoryLocation,
    InventoryMovement,
    InventoryMovementLine,
    InventoryOnHand,
    StockReservation,
)
from app.inventory.schemas import (
    AvailabilityRead,
    LocationAvailability,
    LocationBalance,
    MovementCreate,
    MovementLineRead,
    MovementRead,
    OnHandRead,
    ReservationCreate,
    ReservationRead,
)
from app.products.models import Item, ItemRevision


def request_hash(payload: MovementCreate) -> str:
    canonical = payload.model_dump(mode="json")
    # Numerically equal decimals and timezone-equivalent dates identify the same request.
    for line, original in zip(canonical["lines"], payload.lines):
        line["quantity"] = format(original.quantity.normalize(), "f")
    if payload.occurred_at is not None:
        canonical["occurred_at"] = payload.occurred_at.astimezone(timezone.utc).isoformat()
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode()).hexdigest()


def movement_response(session: Session, movement: InventoryMovement) -> MovementRead:
    lines = session.scalars(select(InventoryMovementLine).where(
        InventoryMovementLine.organization_id == movement.organization_id,
        InventoryMovementLine.movement_id == movement.id,
    ).order_by(InventoryMovementLine.id)).all()
    return MovementRead(
        id=movement.id, organization_id=movement.organization_id,
        movement_type=movement.movement_type, reference=movement.reference,
        occurred_at=movement.occurred_at.astimezone(timezone.utc),
        created_at=movement.created_at.astimezone(timezone.utc),
        lines=[MovementLineRead.model_validate(line) for line in lines],
    )


def post_movement(session: Session, organization_id: int, payload: MovementCreate, idempotency_key: str | None):
    # The existing DB stock triggers take the same transaction lock.
    # Acquire it before retry lookup, and keep it through all line inserts and commit.
    session.execute(select(func.pg_advisory_xact_lock(organization_id)))
    fingerprint = request_hash(payload) if idempotency_key else None
    if idempotency_key:
        previous = session.scalar(select(InventoryMovement).where(
            InventoryMovement.organization_id == organization_id,
            InventoryMovement.idempotency_key == idempotency_key,
        ))
        if previous is not None:
            if previous.request_hash != fingerprint:
                raise DomainError(409, "idempotency_conflict", "This Idempotency-Key was already used for a different movement.")
            return movement_response(session, previous)

    revision_ids = {line.item_revision_id for line in payload.lines}
    known_revisions = set(session.scalars(select(ItemRevision.id).where(
        ItemRevision.organization_id == organization_id, ItemRevision.id.in_(revision_ids),
    )))
    if missing := revision_ids - known_revisions:
        raise DomainError(404, "revision_not_found", f"Item revision {min(missing)} was not found.")

    location_ids = {
        location_id for line in payload.lines
        for location_id in (line.from_location_id, line.to_location_id) if location_id is not None
    }
    known_locations = set(session.scalars(select(InventoryLocation.id).where(
        InventoryLocation.organization_id == organization_id, InventoryLocation.id.in_(location_ids),
    )))
    if missing := location_ids - known_locations:
        raise DomainError(404, "location_not_found", f"Inventory location {min(missing)} was not found.")

    values = payload.model_dump(exclude={"lines", "occurred_at"})
    if payload.occurred_at is not None:
        values["occurred_at"] = payload.occurred_at
    movement = InventoryMovement(
        organization_id=organization_id, idempotency_key=idempotency_key,
        request_hash=fingerprint, **values,
    )
    session.add(movement)
    session.flush()
    for line in payload.lines:
        session.add(InventoryMovementLine(
            organization_id=organization_id, movement_id=movement.id, **line.model_dump(),
        ))
        # Validate in request order. A later failure rolls back this entire request,
        # including the header and earlier lines; no per-line commits or savepoints.
        session.flush()
    logger.info(
        "Posted inventory movement %s (id=%s, type=%s) with %s line(s)",
        movement.reference, movement.id, movement.movement_type, len(payload.lines),
    )
    return movement_response(session, movement)


def on_hand(session: Session, organization_id: int, revision_id: int) -> OnHandRead:
    revision_and_item = session.execute(select(ItemRevision, Item)
        .join(Item, ItemRevision.item_id == Item.id)
        .where(ItemRevision.organization_id == organization_id, ItemRevision.id == revision_id)).first()
    if revision_and_item is None:
        raise DomainError(404, "revision_not_found", "Item revision was not found.")
    revision, item = revision_and_item
    # All locations and totals come from this one balance snapshot.
    rows = session.execute(select(InventoryOnHand, InventoryLocation)
        .join(InventoryLocation, InventoryOnHand.location_id == InventoryLocation.id)
        .where(InventoryOnHand.organization_id == organization_id,
               InventoryOnHand.item_revision_id == revision_id)
        .order_by(InventoryLocation.id)).all()
    locations = [
        LocationBalance(
            location_id=location.id, code=location.code, name=location.name,
            incoming_quantity=balance.incoming_quantity,
            outgoing_quantity=balance.outgoing_quantity,
            on_hand_quantity=balance.on_hand_quantity,
        )
        for balance, location in rows
    ]
    return OnHandRead(
        organization_id=organization_id, item_revision_id=revision_id, item_id=item.id,
        sku=item.sku, revision_code=revision.revision_code, base_uom=item.base_uom,
        incoming_quantity=sum((row.incoming_quantity for row in locations), Decimal(0)),
        outgoing_quantity=sum((row.outgoing_quantity for row in locations), Decimal(0)),
        on_hand_quantity=sum((row.on_hand_quantity for row in locations), Decimal(0)),
        locations=locations,
    )


def availability(session: Session, organization_id: int, revision_id: int) -> AvailabilityRead:
    revision_and_item = session.execute(select(ItemRevision, Item)
        .join(Item, ItemRevision.item_id == Item.id)
        .where(ItemRevision.organization_id == organization_id, ItemRevision.id == revision_id)).first()
    if revision_and_item is None:
        raise DomainError(404, "revision_not_found", "Item revision was not found.")
    revision, item = revision_and_item

    rows = session.execute(select(InventoryAvailability, InventoryLocation)
        .join(InventoryLocation, InventoryAvailability.location_id == InventoryLocation.id)
        .where(InventoryAvailability.organization_id == organization_id,
               InventoryAvailability.item_revision_id == revision_id)
        .order_by(InventoryLocation.id)).all()
    locations = [
        LocationAvailability(
            location_id=location.id, code=location.code, name=location.name,
            on_hand=balance.on_hand_quantity,
            reserved=balance.reserved_quantity,
            available=balance.available_quantity,
        )
        for balance, location in rows
    ]
    total_on_hand = sum((row.on_hand for row in locations), Decimal(0))
    total_reserved = sum((row.reserved for row in locations), Decimal(0))
    total_available = total_on_hand - total_reserved
    return AvailabilityRead(
        organization_id=organization_id, item_revision_id=revision_id, item_id=item.id,
        sku=item.sku, revision_code=revision.revision_code, base_uom=item.base_uom,
        on_hand=total_on_hand,
        reserved=total_reserved,
        available=total_available,
        locations=locations,
    )


def get_reservable_availability(
    session: Session, organization_id: int, revision_ids: Sequence[int]
) -> dict[int, Decimal]:
    """Returns a mapping of revision_id -> total available quantity in active, reservable locations.

    Locations must be active and of type WAREHOUSE, BIN, or PRODUCTION.
    Quarantine, Damaged, and Transit locations are excluded.
    """
    clean_ids = [rid for rid in revision_ids if rid is not None]
    if not clean_ids:
        return {}
    rows = session.execute(
        select(
            InventoryAvailability.item_revision_id,
            func.coalesce(func.sum(func.greatest(0, InventoryAvailability.available_quantity)), 0),
        )
        .join(InventoryLocation, InventoryAvailability.location_id == InventoryLocation.id)
        .where(
            InventoryAvailability.organization_id == organization_id,
            InventoryAvailability.item_revision_id.in_(clean_ids),
            InventoryLocation.is_active == True,
            InventoryLocation.location_type.in_(["WAREHOUSE", "BIN", "PRODUCTION"]),
        )
        .group_by(InventoryAvailability.item_revision_id)
    ).all()
    result = {rev_id: Decimal(0) for rev_id in clean_ids}
    for rev_id, avail_qty in rows:
        result[rev_id] = Decimal(str(avail_qty))
    return result


def create_reservation(session: Session, organization_id: int, payload: ReservationCreate) -> ReservationRead:
    # All stock writes serialize per organization.
    session.execute(select(func.pg_advisory_xact_lock(organization_id)))

    revision = session.scalar(select(ItemRevision.id).where(
        ItemRevision.organization_id == organization_id,
        ItemRevision.id == payload.item_revision_id,
    ))
    if revision is None:
        raise DomainError(404, "revision_not_found", f"Item revision {payload.item_revision_id} was not found.")

    location = session.scalar(select(InventoryLocation).where(
        InventoryLocation.organization_id == organization_id,
        InventoryLocation.id == payload.location_id,
    ))
    if location is None:
        raise DomainError(404, "location_not_found", f"Inventory location {payload.location_id} was not found.")
    if not location.is_active or location.location_type not in ("WAREHOUSE", "BIN", "PRODUCTION"):
        raise DomainError(422, "location_not_reservable", "Stock location is not reservable.")

    current_available = session.scalar(select(InventoryAvailability.available_quantity).where(
        InventoryAvailability.organization_id == organization_id,
        InventoryAvailability.item_revision_id == payload.item_revision_id,
        InventoryAvailability.location_id == payload.location_id,
    ))
    if (current_available or Decimal(0)) < payload.quantity:
        raise DomainError(409, "insufficient_stock", "Insufficient available stock.")

    reservation = StockReservation(
        organization_id=organization_id,
        item_revision_id=payload.item_revision_id,
        location_id=payload.location_id,
        sales_order_line_id=payload.sales_order_line_id,
        production_order_material_id=payload.production_order_material_id,
        quantity=payload.quantity,
        status="ACTIVE",
    )
    session.add(reservation)
    session.flush()
    logger.info(
        "Created active reservation %s (item_revision=%s, location=%s, qty=%s)",
        reservation.id, reservation.item_revision_id, reservation.location_id, reservation.quantity,
    )
    return ReservationRead.model_validate(reservation)


def release_reservation(session: Session, organization_id: int, reservation_id: int) -> ReservationRead:
    session.execute(select(func.pg_advisory_xact_lock(organization_id)))

    reservation = session.scalar(select(StockReservation).where(
        StockReservation.organization_id == organization_id,
        StockReservation.id == reservation_id,
    ))
    if reservation is None:
        raise DomainError(404, "reservation_not_found", f"Reservation {reservation_id} was not found.")

    if reservation.status == "RELEASED":
        # Repeated release is safe and idempotent.
        return ReservationRead.model_validate(reservation)

    if reservation.status == "CONSUMED":
        raise DomainError(409, "reservation_terminal", "A terminal reservation cannot change status.")

    reservation.status = "RELEASED"
    session.flush()
    logger.info("Released reservation %s (item_revision=%s, qty=%s)", reservation.id, reservation.item_revision_id, reservation.quantity)
    return ReservationRead.model_validate(reservation)


def consume_reservation(session: Session, organization_id: int, reservation_id: int) -> ReservationRead:
    session.execute(select(func.pg_advisory_xact_lock(organization_id)))

    reservation = session.scalar(select(StockReservation).where(
        StockReservation.organization_id == organization_id,
        StockReservation.id == reservation_id,
    ))
    if reservation is None:
        raise DomainError(404, "reservation_not_found", f"Reservation {reservation_id} was not found.")

    if reservation.status == "CONSUMED":
        return ReservationRead.model_validate(reservation)

    if reservation.status == "RELEASED":
        raise DomainError(409, "reservation_terminal", "A terminal reservation cannot change status.")

    reservation.status = "CONSUMED"
    session.flush()
    logger.info("Consumed reservation %s (item_revision=%s, qty=%s)", reservation.id, reservation.item_revision_id, reservation.quantity)
    return ReservationRead.model_validate(reservation)


def get_reservation(session: Session, organization_id: int, reservation_id: int) -> ReservationRead:
    reservation = session.scalar(select(StockReservation).where(
        StockReservation.organization_id == organization_id,
        StockReservation.id == reservation_id,
    ))
    if reservation is None:
        raise DomainError(404, "reservation_not_found", f"Reservation {reservation_id} was not found.")
    return ReservationRead.model_validate(reservation)

