"""Product queries and atomic business operations, independent of HTTP."""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.errors import DomainError
from app.products.models import BOM, BOMLine, Item, ItemRevision
from app.products.schemas import (
    BOMCreate, BOMLineCreate, BOMLineRead, BOMRead, BOMSummary,
    ItemCreate, RevisionCreate, RevisionDescription, RevisionRead,
)


def get_item(session: Session, organization_id: int, item_id: int) -> Item:
    item = session.scalar(select(Item).where(Item.id == item_id, Item.organization_id == organization_id))
    if item is None:
        raise DomainError(404, "item_not_found", "Item was not found.")
    return item


def get_revision(session: Session, organization_id: int, revision_id: int) -> ItemRevision:
    revision = session.scalar(select(ItemRevision).where(
        ItemRevision.id == revision_id, ItemRevision.organization_id == organization_id,
    ))
    if revision is None:
        raise DomainError(404, "revision_not_found", "Item revision was not found.")
    return revision


def get_bom(session: Session, organization_id: int, bom_id: int, *, lock=False) -> BOM:
    query = select(BOM).where(BOM.id == bom_id, BOM.organization_id == organization_id)
    if lock:
        query = query.with_for_update()
    bom = session.scalar(query)
    if bom is None:
        raise DomainError(404, "bom_not_found", "BOM was not found.")
    return bom


def create_item(session: Session, organization_id: int, payload: ItemCreate) -> Item:
    item = Item(organization_id=organization_id, **payload.model_dump())
    session.add(item)
    session.flush()
    return item


def create_revision(session: Session, organization_id: int, item_id: int, payload: RevisionCreate):
    get_item(session, organization_id, item_id)
    revision = ItemRevision(organization_id=organization_id, item_id=item_id, **payload.model_dump())
    session.add(revision)
    session.flush()
    return RevisionRead.model_validate(revision)


def list_revisions(session: Session, organization_id: int, item_id: int):
    get_item(session, organization_id, item_id)
    revisions = session.scalars(select(ItemRevision).where(
        ItemRevision.organization_id == organization_id, ItemRevision.item_id == item_id,
    ).order_by(ItemRevision.id)).all()
    boms = session.scalars(select(BOM).join(
        ItemRevision, BOM.product_revision_id == ItemRevision.id,
    ).where(BOM.organization_id == organization_id, ItemRevision.item_id == item_id).order_by(BOM.version)).all()
    by_revision = {}
    for bom in boms:
        by_revision.setdefault(bom.product_revision_id, []).append(BOMSummary.model_validate(bom))
    return [
        RevisionRead.model_validate(revision).model_copy(update={"boms": by_revision.get(revision.id, [])})
        for revision in revisions
    ]


def describe_revision(item: Item, revision: ItemRevision) -> RevisionDescription:
    return RevisionDescription(
        item_id=item.id, sku=item.sku, name=item.name, base_uom=item.base_uom,
        revision_id=revision.id, revision_code=revision.revision_code,
    )


def read_bom(session: Session, organization_id: int, bom_id: int) -> BOMRead:
    bom = get_bom(session, organization_id, bom_id)
    revision = get_revision(session, organization_id, bom.product_revision_id)
    item = get_item(session, organization_id, revision.item_id)
    # One join fetches every component description; no query per BOM line.
    rows = session.execute(select(BOMLine, ItemRevision, Item)
        .join(ItemRevision, BOMLine.component_revision_id == ItemRevision.id)
        .join(Item, ItemRevision.item_id == Item.id)
        .where(BOMLine.organization_id == organization_id, BOMLine.bom_id == bom.id)
        .order_by(BOMLine.line_no)).all()
    lines = [
        BOMLineRead(
            id=line.id, organization_id=line.organization_id, bom_id=line.bom_id,
            line_no=line.line_no, component_revision_id=line.component_revision_id,
            quantity=line.quantity, component=describe_revision(component, component_revision),
        )
        for line, component_revision, component in rows
    ]
    return BOMRead(
        **BOMSummary.model_validate(bom).model_dump(),
        product=describe_revision(item, revision), lines=lines,
    )


def create_bom(session: Session, organization_id: int, payload: BOMCreate):
    get_revision(session, organization_id, payload.product_revision_id)
    bom = BOM(organization_id=organization_id, **payload.model_dump())
    session.add(bom)
    session.flush()
    return read_bom(session, organization_id, bom.id)


def add_bom_line(session: Session, organization_id: int, bom_id: int, payload: BOMLineCreate):
    # Serialize line edits with activation. Database triggers enforce the recipe rules.
    get_bom(session, organization_id, bom_id, lock=True)
    revision = get_revision(session, organization_id, payload.component_revision_id)
    item = get_item(session, organization_id, revision.item_id)
    line = BOMLine(organization_id=organization_id, bom_id=bom_id, **payload.model_dump())
    session.add(line)
    session.flush()
    return BOMLineRead(
        id=line.id, organization_id=organization_id, bom_id=bom_id,
        line_no=line.line_no, component_revision_id=line.component_revision_id,
        quantity=line.quantity, component=describe_revision(item, revision),
    )


def activate_bom(session: Session, organization_id: int, bom_id: int):
    bom = get_bom(session, organization_id, bom_id, lock=True)
    # Repeating activation is safe; obsolete recipes cannot become active again.
    if bom.status != "ACTIVE":
        bom.status = "ACTIVE"
        session.flush()
    return read_bom(session, organization_id, bom_id)
