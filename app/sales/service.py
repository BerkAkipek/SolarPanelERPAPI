"""Business operations for sales orders and order confirmation."""
from datetime import timezone
from decimal import Decimal
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.errors import DomainError
from app.inventory.models import StockReservation
from app.inventory.service import get_reservable_availability
from app.production.service import check_material_feasibility
from app.products.models import BOM, BOMLine, Item, ItemRevision
from app.sales.models import BusinessPartner, SalesOrder, SalesOrderLine
from app.sales.schemas import (
    FulfillmentAnalysisRead,
    LineFulfillmentAnalysis,
    SalesOrderCreate,
    SalesOrderFeasibilityRead,
    SalesOrderLineCreate,
    SalesOrderLineFeasibilityRead,
    SalesOrderLineRead,
    SalesOrderRead,
)



def validate_revision_for_order(session: Session, organization_id: int, item_revision_id: int):
    rev_and_item = session.execute(
        select(ItemRevision, Item)
        .join(Item, ItemRevision.item_id == Item.id)
        .where(
            ItemRevision.organization_id == organization_id,
            ItemRevision.id == item_revision_id,
        )
    ).first()
    if rev_and_item is None:
        raise DomainError(404, "revision_not_found", f"Item revision {item_revision_id} was not found.")
    rev, item = rev_and_item
    if rev.status != "ACTIVE":
        raise DomainError(422, "invalid_revision_status", f"Item revision {item_revision_id} is not ACTIVE.")
    if not item.is_active:
        raise DomainError(422, "item_inactive", f"Item '{item.sku}' is inactive.")
    return rev, item


def create_sales_order(session: Session, organization_id: int, payload: SalesOrderCreate) -> SalesOrderRead:
    customer = session.scalar(
        select(BusinessPartner).where(
            BusinessPartner.organization_id == organization_id,
            BusinessPartner.id == payload.customer_id,
        )
    )
    if customer is None:
        raise DomainError(404, "customer_not_found", f"Customer {payload.customer_id} was not found.")
    if not customer.is_active:
        raise DomainError(422, "customer_inactive", f"Customer {payload.customer_id} is inactive.")

    existing = session.scalar(
        select(SalesOrder.id).where(
            SalesOrder.organization_id == organization_id,
            SalesOrder.order_number == payload.order_number,
        )
    )
    if existing is not None:
        raise DomainError(409, "duplicate_order_number", f"Order number '{payload.order_number}' already exists in this organization.")

    # Validate all requested lines upfront
    for line in payload.lines:
        validate_revision_for_order(session, organization_id, line.item_revision_id)

    order = SalesOrder(
        organization_id=organization_id,
        order_number=payload.order_number,
        customer_id=payload.customer_id,
        currency_code=payload.currency_code,
        ordered_at=payload.ordered_at or func.now(),
        required_at=payload.required_at,
        status="DRAFT",
    )
    session.add(order)
    session.flush()

    # Preserve explicit numbers; omitted numbers use the next free positive number.
    used_numbers = {line.line_no for line in payload.lines if line.line_no is not None}
    next_line = 1
    for line in payload.lines:
        line_no = line.line_no
        if line_no is None:
            while next_line in used_numbers:
                next_line += 1
            line_no = next_line
            used_numbers.add(line_no)
        so_line = SalesOrderLine(
            organization_id=organization_id,
            sales_order_id=order.id,
            line_no=line_no,
            item_revision_id=line.item_revision_id,
            quantity=line.quantity,
            unit_price=line.unit_price,
        )
        session.add(so_line)
    session.flush()

    return read_sales_order(session, organization_id, order.id)


def read_sales_order(session: Session, organization_id: int, order_id: int) -> SalesOrderRead:
    order = session.scalar(
        select(SalesOrder).where(
            SalesOrder.organization_id == organization_id,
            SalesOrder.id == order_id,
        )
    )
    if order is None:
        raise DomainError(404, "order_not_found", f"Sales order {order_id} was not found.")

    rows = session.execute(
        select(SalesOrderLine, ItemRevision, Item)
        .join(ItemRevision, SalesOrderLine.item_revision_id == ItemRevision.id)
        .join(Item, ItemRevision.item_id == Item.id)
        .where(
            SalesOrderLine.organization_id == organization_id,
            SalesOrderLine.sales_order_id == order_id,
        )
        .order_by(SalesOrderLine.line_no)
    ).all()

    lines = [
        SalesOrderLineRead(
            id=line.id,
            organization_id=line.organization_id,
            sales_order_id=line.sales_order_id,
            line_no=line.line_no,
            item_revision_id=line.item_revision_id,
            sku=item.sku,
            revision_code=rev.revision_code,
            quantity=line.quantity,
            unit_price=line.unit_price,
        )
        for line, rev, item in rows
    ]

    return SalesOrderRead(
        id=order.id,
        organization_id=order.organization_id,
        order_number=order.order_number,
        customer_id=order.customer_id,
        status=order.status,
        currency_code=order.currency_code,
        ordered_at=order.ordered_at.astimezone(timezone.utc),
        required_at=order.required_at.astimezone(timezone.utc) if order.required_at else None,
        created_at=order.created_at.astimezone(timezone.utc),
        lines=lines,
    )


def add_sales_order_line(
    session: Session, organization_id: int, order_id: int, payload: SalesOrderLineCreate
) -> SalesOrderLineRead:
    order = session.scalar(
        select(SalesOrder)
        .where(SalesOrder.organization_id == organization_id, SalesOrder.id == order_id)
        .with_for_update()
    )
    if order is None:
        raise DomainError(404, "order_not_found", f"Sales order {order_id} was not found.")
    if order.status != "DRAFT":
        raise DomainError(409, "order_locked", "Only draft sales orders can be edited.")

    rev, item = validate_revision_for_order(session, organization_id, payload.item_revision_id)

    if payload.line_no is not None:
        line_no = payload.line_no
    else:
        max_line = session.scalar(
            select(func.max(SalesOrderLine.line_no)).where(
                SalesOrderLine.organization_id == organization_id,
                SalesOrderLine.sales_order_id == order_id,
            )
        ) or 0
        if max_line == 2147483647:
            raise DomainError(409, "line_number_exhausted", "Automatic line numbering is exhausted; specify an unused line_no.")
        line_no = max_line + 1

    so_line = SalesOrderLine(
        organization_id=organization_id,
        sales_order_id=order_id,
        line_no=line_no,
        item_revision_id=payload.item_revision_id,
        quantity=payload.quantity,
        unit_price=payload.unit_price,
    )
    session.add(so_line)
    session.flush()

    return SalesOrderLineRead(
        id=so_line.id,
        organization_id=so_line.organization_id,
        sales_order_id=so_line.sales_order_id,
        line_no=so_line.line_no,
        item_revision_id=so_line.item_revision_id,
        sku=item.sku,
        revision_code=rev.revision_code,
        quantity=so_line.quantity,
        unit_price=so_line.unit_price,
    )


def confirm_sales_order(session: Session, organization_id: int, order_id: int) -> SalesOrderRead:
    order = session.scalar(
        select(SalesOrder)
        .where(SalesOrder.organization_id == organization_id, SalesOrder.id == order_id)
        .with_for_update()
    )
    if order is None:
        raise DomainError(404, "order_not_found", f"Sales order {order_id} was not found.")

    if order.status == "CANCELLED":
        raise DomainError(409, "order_cancelled", "A cancelled sales order cannot be confirmed.")

    if order.status == "CONFIRMED":
        # Repeating confirmation is safe and idempotent.
        return read_sales_order(session, organization_id, order_id)

    if order.status != "DRAFT":
        raise DomainError(409, "order_invalid_status", f"Cannot confirm sales order with status '{order.status}'.")

    line_count = session.scalar(
        select(func.count(SalesOrderLine.id)).where(
            SalesOrderLine.organization_id == organization_id,
            SalesOrderLine.sales_order_id == order_id,
        )
    )
    if line_count == 0:
        raise DomainError(422, "empty_order", "Add at least one line before confirming the sales order.")

    # Verify all lines have active revisions
    lines = session.scalars(
        select(SalesOrderLine).where(
            SalesOrderLine.organization_id == organization_id,
            SalesOrderLine.sales_order_id == order_id,
        )
    ).all()
    for line in lines:
        validate_revision_for_order(session, organization_id, line.item_revision_id)

    order.status = "CONFIRMED"
    session.flush()
    logger.info("Confirmed sales order %s (id=%s) with %s line(s)", order.order_number, order.id, len(lines))

    return read_sales_order(session, organization_id, order_id)


def cancel_sales_order(session: Session, organization_id: int, order_id: int) -> SalesOrderRead:
    # Match reservation creation's lock order: organization stock lock, then order.
    session.execute(select(func.pg_advisory_xact_lock(organization_id)))
    order = session.scalar(
        select(SalesOrder)
        .where(SalesOrder.organization_id == organization_id, SalesOrder.id == order_id)
        .with_for_update()
    )
    if order is None:
        raise DomainError(404, "order_not_found", f"Sales order {order_id} was not found.")

    if order.status == "CANCELLED":
        return read_sales_order(session, organization_id, order_id)

    if order.status in ("SHIPPED", "PARTIALLY_SHIPPED"):
        raise DomainError(409, "order_shipped", f"Cannot cancel a {order.status.lower()} sales order.")
    if order.status not in ("DRAFT", "CONFIRMED"):
        raise DomainError(409, "order_invalid_status", f"Cannot cancel sales order with status '{order.status}'.")

    has_reservations = session.scalar(
        select(func.count(StockReservation.id))
        .join(SalesOrderLine, StockReservation.sales_order_line_id == SalesOrderLine.id)
        .where(
            SalesOrderLine.organization_id == organization_id,
            SalesOrderLine.sales_order_id == order_id,
            StockReservation.status.in_(["ACTIVE", "CONSUMED"]),
        )
    )
    if has_reservations:
        raise DomainError(409, "order_has_reservations", "Orders with active or consumed reservations cannot be cancelled. Active reservations may be released; consumed reservations are terminal.")

    order.status = "CANCELLED"
    session.flush()
    logger.info("Cancelled sales order %s (id=%s)", order.order_number, order.id)

    return read_sales_order(session, organization_id, order_id)


def analyze_sales_order_fulfillment(
    session: Session, organization_id: int, order_id: int
) -> FulfillmentAnalysisRead:
    order = session.scalar(
        select(SalesOrder).where(
            SalesOrder.organization_id == organization_id,
            SalesOrder.id == order_id,
        )
    )
    if order is None:
        raise DomainError(404, "order_not_found", f"Sales order {order_id} was not found.")

    rows = session.execute(
        select(SalesOrderLine, ItemRevision, Item)
        .join(ItemRevision, SalesOrderLine.item_revision_id == ItemRevision.id)
        .join(Item, ItemRevision.item_id == Item.id)
        .where(
            SalesOrderLine.organization_id == organization_id,
            SalesOrderLine.sales_order_id == order_id,
        )
        .order_by(SalesOrderLine.line_no)
    ).all()

    # Inventory owns eligibility and reservation math. Read every revision's stock
    # in one query, then allocate the resulting snapshot in sales-line order.
    available_cache = get_reservable_availability(
        session, organization_id, list({line.item_revision_id for line, _, _ in rows}),
    )

    analysis_lines = []
    total_ordered = Decimal(0)
    total_from_stock = Decimal(0)
    total_production_required = Decimal(0)

    for line, rev, item in rows:
        ordered_qty = line.quantity
        avail_stock = available_cache.get(line.item_revision_id, Decimal(0))

        ship_from_stock = min(ordered_qty, avail_stock)
        production_required = max(Decimal(0), ordered_qty - ship_from_stock)

        available_cache[line.item_revision_id] = avail_stock - ship_from_stock

        total_ordered += ordered_qty
        total_from_stock += ship_from_stock
        total_production_required += production_required

        analysis_lines.append(
            LineFulfillmentAnalysis(
                line_id=line.id,
                line_no=line.line_no,
                item_revision_id=line.item_revision_id,
                sku=item.sku,
                revision_code=rev.revision_code,
                ordered_quantity=ordered_qty,
                available_quantity=avail_stock,
                ship_from_stock=ship_from_stock,
                production_required=production_required,
                ordered=ordered_qty,
                available=avail_stock,
            )
        )

    can_fulfill_entirely = total_production_required == Decimal(0) and len(analysis_lines) > 0

    return FulfillmentAnalysisRead(
        sales_order_id=order.id,
        order_number=order.order_number,
        status=order.status,
        can_fulfill_entirely_from_stock=can_fulfill_entirely,
        total_ordered=total_ordered,
        total_from_stock=total_from_stock,
        total_production_required=total_production_required,
        lines=analysis_lines,
    )


def analyze_sales_order_material_feasibility(
    session: Session, organization_id: int, order_id: int
) -> SalesOrderFeasibilityRead:
    """Read-only evaluation combining sales order fulfillment with BOM explosion and component stock."""
    fulfillment = analyze_sales_order_fulfillment(session, organization_id, order_id)

    # Select the latest active recipe per demanded revision, then read one shared
    # component stock snapshot for the whole order.
    revisions = {line.item_revision_id for line in fulfillment.lines if line.production_required > 0}
    recipes = session.scalars(select(BOM).where(
        BOM.organization_id == organization_id,
        BOM.product_revision_id.in_(revisions), BOM.status == "ACTIVE",
    ).order_by(BOM.version.desc())).all()
    by_revision = {}
    for recipe in recipes:
        by_revision.setdefault(recipe.product_revision_id, recipe)
    component_ids = session.scalars(select(BOMLine.component_revision_id).where(
        BOMLine.organization_id == organization_id,
        BOMLine.bom_id.in_([recipe.id for recipe in by_revision.values()]),
    ).distinct()).all()
    remaining = get_reservable_availability(session, organization_id, component_ids)
    for line in fulfillment.lines:
        if line.item_revision_id in remaining:
            remaining[line.item_revision_id] = max(Decimal(0), remaining[line.item_revision_id] - line.ship_from_stock)

    feasibility_lines: list[SalesOrderLineFeasibilityRead] = []

    for line in fulfillment.lines:
        if line.production_required <= Decimal(0):
            feasibility_lines.append(
                SalesOrderLineFeasibilityRead(
                    line_id=line.line_id,
                    line_no=line.line_no,
                    item_revision_id=line.item_revision_id,
                    sku=line.sku,
                    revision_code=line.revision_code,
                    ordered_quantity=line.ordered_quantity,
                    ship_from_stock=line.ship_from_stock,
                    production_required=line.production_required,
                    bom_id=None,
                    can_produce=True,
                    status_message="Fulfilled entirely from stock",
                    materials=[],
                )
            )
            continue

        active_bom = by_revision.get(line.item_revision_id)

        if active_bom is None:
            feasibility_lines.append(
                SalesOrderLineFeasibilityRead(
                    line_id=line.line_id,
                    line_no=line.line_no,
                    item_revision_id=line.item_revision_id,
                    sku=line.sku,
                    revision_code=line.revision_code,
                    ordered_quantity=line.ordered_quantity,
                    ship_from_stock=line.ship_from_stock,
                    production_required=line.production_required,
                    bom_id=None,
                    can_produce=False,
                    status_message="No active BOM configured for this revision",
                    materials=[],
                )
            )
            continue

        bom_feasibility = check_material_feasibility(
            session, organization_id, active_bom.id, line.production_required,
            available_quantities=remaining,
        )
        for material in bom_feasibility.materials:
            remaining[material.component_revision_id] = max(Decimal(0), material.available - material.required)

        feasibility_lines.append(
            SalesOrderLineFeasibilityRead(
                line_id=line.line_id,
                line_no=line.line_no,
                item_revision_id=line.item_revision_id,
                sku=line.sku,
                revision_code=line.revision_code,
                ordered_quantity=line.ordered_quantity,
                ship_from_stock=line.ship_from_stock,
                production_required=line.production_required,
                bom_id=active_bom.id,
                can_produce=bom_feasibility.can_produce,
                status_message="Production feasible" if bom_feasibility.can_produce else "Component shortages detected",
                materials=bom_feasibility.materials,
            )
        )

    can_fulfill_all = (
        len(feasibility_lines) > 0 and all(l.can_produce for l in feasibility_lines)
    )

    return SalesOrderFeasibilityRead(
        sales_order_id=fulfillment.sales_order_id,
        order_number=fulfillment.order_number,
        status=fulfillment.status,
        can_fulfill_all=can_fulfill_all,
        can_produce=can_fulfill_all,
        lines=feasibility_lines,
    )
