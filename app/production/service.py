from datetime import datetime, timezone
from decimal import Decimal
import logging

from sqlalchemy import func, select
from sqlalchemy.orm import Session

logger = logging.getLogger(__name__)

from app.errors import DomainError
from app.inventory.models import (
    InventoryAvailability,
    InventoryLocation,
    InventoryMovement,
    InventoryMovementLine,
    StockReservation,
)
from app.inventory.schemas import ReservationCreate
from app.inventory.service import create_reservation, get_reservable_availability
from app.production.domain import evaluate_material_feasibility, explode_bom
from app.production.models import ProductionOrder, ProductionOrderMaterial
from app.production.schemas import (
    BOMExplosionRead,
    ExplodedRequirementRead,
    MaterialFeasibilityItem,
    MaterialFeasibilityRead,
    ProductionOrderComplete,
    ProductionOrderCreate,
    ProductionOrderMaterialRead,
    ProductionOrderRead,
    ProductionOrderRelease,
)
from app.products.models import BOM, BOMLine, Item, ItemRevision
from app.products.service import read_bom
from app.sales.models import SalesOrder, SalesOrderLine


def explode_bom_recipe(
    session: Session, organization_id: int, bom_id: int, production_quantity: Decimal
) -> BOMExplosionRead:
    bom = read_bom(session, organization_id, bom_id)
    requirements = explode_bom(bom, production_quantity)
    return BOMExplosionRead(
        bom_id=bom_id,
        production_quantity=production_quantity,
        requirements=[
            ExplodedRequirementRead(
                bom_line_id=req.bom_line_id,
                line_no=req.line_no,
                component_revision_id=req.component_revision_id,
                quantity_per_unit=req.quantity_per_unit,
                required_quantity=req.required_quantity,
                sku=req.sku,
                name=req.name,
                base_uom=req.base_uom,
            )
            for req in requirements
        ],
    )


def check_material_feasibility(
    session: Session, organization_id: int, bom_id: int, production_quantity: Decimal
) -> MaterialFeasibilityRead:
    """Read-only evaluation of material feasibility against physical reservable stock."""
    bom = read_bom(session, organization_id, bom_id)
    requirements = explode_bom(bom, production_quantity)
    comp_rev_ids = [r.component_revision_id for r in requirements if r.component_revision_id is not None]
    availabilities = get_reservable_availability(session, organization_id, comp_rev_ids)
    feasibility = evaluate_material_feasibility(requirements, availabilities, production_quantity)
    return MaterialFeasibilityRead(
        bom_id=bom_id,
        production_required=feasibility.production_required,
        can_produce=feasibility.can_produce,
        materials=[
            MaterialFeasibilityItem(
                component_revision_id=m.component_revision_id,
                sku=m.sku,
                name=m.name,
                base_uom=m.base_uom,
                required=m.required,
                available=m.available,
                shortage=m.shortage,
            )
            for m in feasibility.materials
        ],
    )


def create_production_order(
    session: Session, organization_id: int, payload: ProductionOrderCreate
) -> ProductionOrderRead:
    """Creates a production order in DRAFT status and snapshots material requirements within one transaction."""
    session.execute(select(func.pg_advisory_xact_lock(organization_id)))

    # Resolve demand linkage if source_sales_order_line_id is given
    source_line: SalesOrderLine | None = None
    if payload.source_sales_order_line_id is not None:
        source_line = session.scalar(
            select(SalesOrderLine).where(
                SalesOrderLine.organization_id == organization_id,
                SalesOrderLine.id == payload.source_sales_order_line_id,
            )
        )
        if source_line is None:
            raise DomainError(
                404,
                "sales_order_line_not_found",
                f"Sales order line {payload.source_sales_order_line_id} was not found.",
            )
        so = session.scalar(
            select(SalesOrder).where(
                SalesOrder.organization_id == organization_id,
                SalesOrder.id == source_line.sales_order_id,
            )
        )
        if so and so.status == "CANCELLED":
            raise DomainError(
                409, "order_cancelled", "Cannot create production order for a cancelled sales order."
            )

        if payload.product_revision_id is not None and payload.product_revision_id != source_line.item_revision_id:
            raise DomainError(
                422,
                "mismatched_revision",
                "Specified product_revision_id does not match sales order line revision.",
            )
        product_revision_id = source_line.item_revision_id
    else:
        if payload.product_revision_id is None:
            raise DomainError(
                422,
                "missing_product_revision",
                "Either source_sales_order_line_id or product_revision_id must be provided.",
            )
        product_revision_id = payload.product_revision_id

    # Validate product revision
    rev_and_item = session.execute(
        select(ItemRevision, Item)
        .join(Item, ItemRevision.item_id == Item.id)
        .where(
            ItemRevision.organization_id == organization_id,
            ItemRevision.id == product_revision_id,
        )
    ).first()
    if rev_and_item is None:
        raise DomainError(404, "revision_not_found", f"Item revision {product_revision_id} was not found.")
    rev, item = rev_and_item
    if rev.status != "ACTIVE":
        raise DomainError(422, "inactive_revision", f"Item revision {product_revision_id} is not ACTIVE.")

    # Resolve BOM
    if payload.bom_id is not None:
        bom = session.scalar(
            select(BOM).where(
                BOM.organization_id == organization_id,
                BOM.id == payload.bom_id,
                BOM.product_revision_id == product_revision_id,
            )
        )
        if bom is None:
            raise DomainError(
                404, "bom_not_found", f"BOM {payload.bom_id} was not found for revision {product_revision_id}."
            )
        if bom.status != "ACTIVE":
            raise DomainError(422, "inactive_bom", "Production requires an ACTIVE BOM.")
    else:
        bom = session.scalar(
            select(BOM)
            .where(
                BOM.organization_id == organization_id,
                BOM.product_revision_id == product_revision_id,
                BOM.status == "ACTIVE",
            )
            .order_by(BOM.version.desc())
        )
        if bom is None:
            raise DomainError(
                422, "no_active_bom", f"No active BOM found for item revision {product_revision_id}."
            )

    # Generate or validate order number
    if payload.production_order_number:
        existing = session.scalar(
            select(ProductionOrder.id).where(
                ProductionOrder.organization_id == organization_id,
                ProductionOrder.production_order_number == payload.production_order_number,
            )
        )
        if existing is not None:
            raise DomainError(
                409,
                "duplicate_order_number",
                f"Production order number '{payload.production_order_number}' already exists.",
            )
        order_number = payload.production_order_number
    else:
        count = (
            session.scalar(
                select(func.count(ProductionOrder.id)).where(
                    ProductionOrder.organization_id == organization_id
                )
            )
            or 0
        )
        candidate = f"PO-{count + 1:05d}"
        while session.scalar(
            select(ProductionOrder.id).where(
                ProductionOrder.organization_id == organization_id,
                ProductionOrder.production_order_number == candidate,
            )
        ):
            count += 1
            candidate = f"PO-{count + 1:05d}"
        order_number = candidate

    # Fetch BOM lines
    bom_lines = session.scalars(
        select(BOMLine)
        .where(
            BOMLine.organization_id == organization_id,
            BOMLine.bom_id == bom.id,
        )
        .order_by(BOMLine.line_no)
    ).all()
    if not bom_lines:
        raise DomainError(422, "empty_bom", "BOM has no component lines.")

    # Pure domain BOM explosion
    exploded = explode_bom(bom_lines, payload.quantity, output_quantity=bom.output_quantity)

    # Within one transaction: snapshot production order header + material lines
    order = ProductionOrder(
        organization_id=organization_id,
        production_order_number=order_number,
        product_revision_id=product_revision_id,
        bom_id=bom.id,
        source_sales_order_line_id=payload.source_sales_order_line_id,
        quantity=payload.quantity,
        status="DRAFT",
        planned_start_at=payload.planned_start_at,
    )
    session.add(order)
    session.flush()

    for line, req in zip(bom_lines, exploded):
        mat = ProductionOrderMaterial(
            organization_id=organization_id,
            production_order_id=order.id,
            bom_id=bom.id,
            bom_line_id=line.id,
            component_revision_id=line.component_revision_id,
            required_quantity=req.required_quantity,
        )
        session.add(mat)
    session.flush()
    logger.info(
        "Created production order %s (id=%s, qty=%s) in DRAFT with %s material line(s)",
        order.production_order_number, order.id, order.quantity, len(bom_lines),
    )

    return read_production_order(session, organization_id, order.id)


def read_production_order(session: Session, organization_id: int, order_id: int) -> ProductionOrderRead:
    order = session.scalar(
        select(ProductionOrder).where(
            ProductionOrder.organization_id == organization_id,
            ProductionOrder.id == order_id,
        )
    )
    if order is None:
        raise DomainError(404, "order_not_found", f"Production order {order_id} was not found.")

    rev = session.scalar(
        select(ItemRevision).where(
            ItemRevision.organization_id == organization_id,
            ItemRevision.id == order.product_revision_id,
        )
    )
    item = session.scalar(select(Item).where(Item.id == rev.item_id)) if rev else None
    bom = session.scalar(
        select(BOM).where(
            BOM.organization_id == organization_id,
            BOM.id == order.bom_id,
        )
    )

    mat_rows = session.execute(
        select(ProductionOrderMaterial, ItemRevision, Item)
        .join(ItemRevision, ProductionOrderMaterial.component_revision_id == ItemRevision.id)
        .join(Item, ItemRevision.item_id == Item.id)
        .where(
            ProductionOrderMaterial.organization_id == organization_id,
            ProductionOrderMaterial.production_order_id == order.id,
        )
        .order_by(ProductionOrderMaterial.id)
    ).all()

    materials_read = []
    for mat, comp_rev, comp_item in mat_rows:
        active_res_qty = (
            session.scalar(
                select(func.coalesce(func.sum(StockReservation.quantity), Decimal(0))).where(
                    StockReservation.organization_id == organization_id,
                    StockReservation.production_order_material_id == mat.id,
                    StockReservation.status == "ACTIVE",
                )
            )
            or Decimal(0)
        )
        consumed_res_qty = (
            session.scalar(
                select(func.coalesce(func.sum(StockReservation.quantity), Decimal(0))).where(
                    StockReservation.organization_id == organization_id,
                    StockReservation.production_order_material_id == mat.id,
                    StockReservation.status == "CONSUMED",
                )
            )
            or Decimal(0)
        )
        materials_read.append(
            ProductionOrderMaterialRead(
                id=mat.id,
                bom_id=mat.bom_id,
                bom_line_id=mat.bom_line_id,
                component_revision_id=mat.component_revision_id,
                sku=comp_item.sku,
                name=comp_item.name,
                base_uom=comp_item.base_uom,
                required_quantity=mat.required_quantity,
                reserved_quantity=active_res_qty,
                consumed_quantity=consumed_res_qty,
            )
        )

    return ProductionOrderRead(
        id=order.id,
        organization_id=order.organization_id,
        production_order_number=order.production_order_number,
        product_revision_id=order.product_revision_id,
        sku=item.sku if item else None,
        revision_code=rev.revision_code if rev else None,
        bom_id=order.bom_id,
        bom_version=bom.version if bom else None,
        source_sales_order_line_id=order.source_sales_order_line_id,
        quantity=order.quantity,
        status=order.status,
        planned_start_at=order.planned_start_at,
        created_at=order.created_at,
        materials=materials_read,
    )


def release_production_order(
    session: Session,
    organization_id: int,
    order_id: int,
    payload: ProductionOrderRelease | None = None,
) -> ProductionOrderRead:
    """Transitions production order from DRAFT to RELEASED and creates stock reservations for required materials."""
    session.execute(select(func.pg_advisory_xact_lock(organization_id)))

    order = session.scalar(
        select(ProductionOrder)
        .where(
            ProductionOrder.organization_id == organization_id,
            ProductionOrder.id == order_id,
        )
        .with_for_update()
    )
    if order is None:
        raise DomainError(404, "order_not_found", f"Production order {order_id} was not found.")

    if order.status == "RELEASED":
        # Idempotent
        return read_production_order(session, organization_id, order.id)

    if order.status in ("COMPLETED", "CANCELLED", "IN_PRODUCTION"):
        raise DomainError(
            409, "order_status_invalid", f"Cannot release a {order.status.lower()} production order."
        )

    materials = session.scalars(
        select(ProductionOrderMaterial)
        .where(
            ProductionOrderMaterial.organization_id == organization_id,
            ProductionOrderMaterial.production_order_id == order.id,
        )
        .order_by(ProductionOrderMaterial.id)
    ).all()

    # Reserve materials
    for mat in materials:
        target_location_id: int | None = None
        if payload and payload.location_id is not None:
            target_location_id = payload.location_id
        else:
            # Find reservable location with enough available stock
            loc = session.execute(
                select(InventoryAvailability.location_id)
                .join(InventoryLocation, InventoryAvailability.location_id == InventoryLocation.id)
                .where(
                    InventoryAvailability.organization_id == organization_id,
                    InventoryAvailability.item_revision_id == mat.component_revision_id,
                    InventoryLocation.is_active == True,
                    InventoryLocation.location_type.in_(["WAREHOUSE", "BIN", "PRODUCTION"]),
                    InventoryAvailability.available_quantity >= mat.required_quantity,
                )
                .order_by(InventoryAvailability.available_quantity.desc())
            ).first()
            if loc:
                target_location_id = loc[0]
            else:
                raise DomainError(
                    409,
                    "insufficient_stock",
                    f"Insufficient available stock to reserve component {mat.component_revision_id} for production order {order.id}.",
                )

        create_reservation(
            session,
            organization_id,
            ReservationCreate(
                item_revision_id=mat.component_revision_id,
                location_id=target_location_id,
                quantity=mat.required_quantity,
                production_order_material_id=mat.id,
            ),
        )

    order.status = "RELEASED"
    session.flush()
    logger.info(
        "Released production order %s (id=%s) and reserved %s material lines",
        order.production_order_number, order.id, len(materials),
    )

    return read_production_order(session, organization_id, order.id)


def cancel_production_order(session: Session, organization_id: int, order_id: int) -> ProductionOrderRead:
    session.execute(select(func.pg_advisory_xact_lock(organization_id)))

    order = session.scalar(
        select(ProductionOrder)
        .where(
            ProductionOrder.organization_id == organization_id,
            ProductionOrder.id == order_id,
        )
        .with_for_update()
    )
    if order is None:
        raise DomainError(404, "order_not_found", f"Production order {order_id} was not found.")

    if order.status == "CANCELLED":
        return read_production_order(session, organization_id, order.id)

    if order.status in ("IN_PRODUCTION", "COMPLETED"):
        raise DomainError(
            409, "order_active", f"Cannot cancel a {order.status.lower()} production order."
        )

    # Release any active reservations tied to this order's materials
    reservations = session.scalars(
        select(StockReservation)
        .join(
            ProductionOrderMaterial,
            StockReservation.production_order_material_id == ProductionOrderMaterial.id,
        )
        .where(
            ProductionOrderMaterial.organization_id == organization_id,
            ProductionOrderMaterial.production_order_id == order.id,
            StockReservation.status == "ACTIVE",
        )
    ).all()
    for res in reservations:
        res.status = "RELEASED"

    order.status = "CANCELLED"
    session.flush()
    logger.info("Cancelled production order %s (id=%s) and released reservations", order.production_order_number, order.id)

    return read_production_order(session, organization_id, order.id)


def create_sales_order_production_order(
    session: Session, organization_id: int, order_id: int, payload: ProductionOrderCreate
) -> ProductionOrderRead:
    """Helper to create a production order scoped to a sales order."""
    if payload.source_sales_order_line_id is not None:
        line = session.scalar(
            select(SalesOrderLine).where(
                SalesOrderLine.organization_id == organization_id,
                SalesOrderLine.sales_order_id == order_id,
                SalesOrderLine.id == payload.source_sales_order_line_id,
            )
        )
        if line is None:
            raise DomainError(
                404,
                "sales_order_line_not_found",
                f"Sales order line {payload.source_sales_order_line_id} was not found on sales order {order_id}.",
            )
    else:
        lines = session.scalars(
            select(SalesOrderLine).where(
                SalesOrderLine.organization_id == organization_id,
                SalesOrderLine.sales_order_id == order_id,
            )
        ).all()
        if len(lines) == 1:
            payload = payload.model_copy(update={"source_sales_order_line_id": lines[0].id})
        elif len(lines) == 0:
            raise DomainError(422, "empty_order", "Sales order has no lines.")
        else:
            raise DomainError(
                422,
                "missing_source_line",
                "Sales order has multiple lines; specify source_sales_order_line_id.",
            )

    return create_production_order(session, organization_id, payload)


def start_production_order(session: Session, organization_id: int, order_id: int) -> ProductionOrderRead:
    """Transitions production order from RELEASED to IN_PROGRESS."""
    session.execute(select(func.pg_advisory_xact_lock(organization_id)))

    order = session.scalar(
        select(ProductionOrder)
        .where(
            ProductionOrder.organization_id == organization_id,
            ProductionOrder.id == order_id,
        )
        .with_for_update()
    )
    if order is None:
        raise DomainError(404, "order_not_found", f"Production order {order_id} was not found.")

    if order.status in ("IN_PROGRESS", "IN_PRODUCTION"):
        return read_production_order(session, organization_id, order.id)

    if order.status != "RELEASED":
        raise DomainError(
            409,
            "invalid_status",
            f"Cannot start production order with status '{order.status}'. Order must be RELEASED first.",
        )

    order.status = "IN_PROGRESS"
    session.flush()
    logger.info("Started production order %s (id=%s) -> IN_PROGRESS", order.production_order_number, order.id)

    return read_production_order(session, organization_id, order.id)


def complete_production_order(
    session: Session,
    organization_id: int,
    order_id: int,
    payload: ProductionOrderComplete | None = None,
) -> ProductionOrderRead:
    """Completes production order atomically within one transaction:

    1. Consumes material reservations (status -> CONSUMED)
    2. Creates PRODUCTION_CONSUMPTION movements for raw materials
    3. Creates PRODUCTION_OUTPUT movement for finished goods
    4. Marks production order COMPLETED

    If any part fails, everything rolls back.
    """
    session.execute(select(func.pg_advisory_xact_lock(organization_id)))

    order = session.scalar(
        select(ProductionOrder)
        .where(
            ProductionOrder.organization_id == organization_id,
            ProductionOrder.id == order_id,
        )
        .with_for_update()
    )
    if order is None:
        raise DomainError(404, "order_not_found", f"Production order {order_id} was not found.")

    if order.status == "COMPLETED":
        # Idempotent
        return read_production_order(session, organization_id, order.id)

    if order.status not in ("RELEASED", "IN_PROGRESS", "IN_PRODUCTION"):
        raise DomainError(
            409,
            "invalid_order_status",
            f"Cannot complete production order with status '{order.status}'. Order must be RELEASED or IN_PROGRESS.",
        )

    # Resolve destination location for finished goods
    if payload and payload.to_location_id is not None:
        dest_loc_id = payload.to_location_id
    else:
        # Pick default active WAREHOUSE location
        dest_loc_id = session.scalar(
            select(InventoryLocation.id).where(
                InventoryLocation.organization_id == organization_id,
                InventoryLocation.is_active == True,
                InventoryLocation.location_type == "WAREHOUSE",
            ).order_by(InventoryLocation.id)
        )
        if dest_loc_id is None:
            dest_loc_id = session.scalar(
                select(InventoryLocation.id).where(
                    InventoryLocation.organization_id == organization_id,
                    InventoryLocation.is_active == True,
                    InventoryLocation.location_type.in_(["WAREHOUSE", "BIN", "PRODUCTION"]),
                ).order_by(InventoryLocation.id)
            )
        if dest_loc_id is None:
            raise DomainError(422, "missing_location", "No active warehouse location found to receive finished goods.")

    dest_loc = session.scalar(
        select(InventoryLocation).where(
            InventoryLocation.organization_id == organization_id,
            InventoryLocation.id == dest_loc_id,
        )
    )
    if not dest_loc or not dest_loc.is_active or dest_loc.location_type not in ("WAREHOUSE", "BIN", "PRODUCTION"):
        raise DomainError(422, "invalid_location", "Destination location must be an active reservable location.")

    materials = session.scalars(
        select(ProductionOrderMaterial)
        .where(
            ProductionOrderMaterial.organization_id == organization_id,
            ProductionOrderMaterial.production_order_id == order.id,
        )
        .order_by(ProductionOrderMaterial.id)
    ).all()

    # Step 1: Consume active material reservations
    mat_reservations: dict[int, StockReservation] = {}
    for mat in materials:
        res = session.scalar(
            select(StockReservation)
            .where(
                StockReservation.organization_id == organization_id,
                StockReservation.production_order_material_id == mat.id,
                StockReservation.status == "ACTIVE",
            )
            .with_for_update()
        )
        if res is None:
            raise DomainError(
                409,
                "missing_reservation",
                f"Active reservation for component revision {mat.component_revision_id} was not found on production order {order.id}.",
            )
        res.status = "CONSUMED"
        mat_reservations[mat.id] = res

    session.flush()

    occurred_at = (payload.occurred_at if payload else None) or datetime.now(timezone.utc)

    # Step 2: Post raw material consumption movements
    consume_movement = InventoryMovement(
        organization_id=organization_id,
        movement_type="PRODUCTION_CONSUMPTION",
        reference=f"PROD-CONSUME-{order.production_order_number}",
        occurred_at=occurred_at,
    )
    session.add(consume_movement)
    session.flush()

    for mat in materials:
        res = mat_reservations[mat.id]
        session.add(
            InventoryMovementLine(
                organization_id=organization_id,
                movement_id=consume_movement.id,
                item_revision_id=mat.component_revision_id,
                from_location_id=res.location_id,
                to_location_id=None,
                quantity=mat.required_quantity,
            )
        )
        session.flush()  # DB trigger validate_movement_balance validates stock balance

    # Step 3: Post finished-good output movement
    output_movement = InventoryMovement(
        organization_id=organization_id,
        movement_type="PRODUCTION_OUTPUT",
        reference=f"PROD-OUTPUT-{order.production_order_number}",
        occurred_at=occurred_at,
    )
    session.add(output_movement)
    session.flush()

    session.add(
        InventoryMovementLine(
            organization_id=organization_id,
            movement_id=output_movement.id,
            item_revision_id=order.product_revision_id,
            from_location_id=None,
            to_location_id=dest_loc_id,
            quantity=order.quantity,
        )
    )
    session.flush()

    # Step 4: Mark production order completed
    order.status = "COMPLETED"
    session.flush()
    logger.info(
        "Completed production order %s (id=%s) -> COMPLETED. Output: %s finished panels to location %s, consumed %s material lines",
        order.production_order_number, order.id, order.quantity, dest_loc_id, len(materials),
    )

    return read_production_order(session, organization_id, order.id)


