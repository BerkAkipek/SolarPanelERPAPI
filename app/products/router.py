"""HTTP interface for step 1: product definition."""
from fastapi import APIRouter

from app.dependencies import DatabaseSession, OrganizationID, PathID
from app.products import service
from app.products.schemas import (
    BOMCreate, BOMLineCreate, BOMLineRead, BOMRead, ItemCreate, ItemRead,
    RevisionCreate, RevisionRead,
)

router = APIRouter(tags=["Product definition"])


@router.post("/items", response_model=ItemRead, status_code=201, summary="Create catalog item")
def create_item(payload: ItemCreate, session: DatabaseSession, organization_id: OrganizationID):
    """Creates a new catalog item (e.g.

    finished solar panel model or component).
    - SKU must be unique per organization.
    - Type must be FINISHED_GOOD, COMPONENT, RAW_MATERIAL, or CONSUMABLE.
    """
    return service.create_item(session, organization_id, payload)


@router.post("/items/{item_id}/revisions", response_model=RevisionRead, status_code=201, summary="Create item revision")
def create_revision(item_id: PathID, payload: RevisionCreate, session: DatabaseSession, organization_id: OrganizationID):
    """Creates a versioned, immutable revision for an item (e.g.

    REV-A).
    - Revision code must be unique for the item.
    - Revisions start in ACTIVE or DRAFT state.
    """
    return service.create_revision(session, organization_id, item_id, payload)


@router.post("/boms", response_model=BOMRead, status_code=201, summary="Create Bill of Materials (recipe)")
def create_bom(payload: BOMCreate, session: DatabaseSession, organization_id: OrganizationID):
    """Creates a new Bill of Materials recipe for a finished-good product revision.

    - Starts in DRAFT status.
    - Database trigger enforces that recipes start in DRAFT and cannot be activated without component lines.
    """
    return service.create_bom(session, organization_id, payload)


@router.post("/boms/{bom_id}/lines", response_model=BOMLineRead, status_code=201, summary="Add component line to BOM")
def add_bom_line(bom_id: PathID, payload: BOMLineCreate, session: DatabaseSession, organization_id: OrganizationID):
    """Adds a required component revision and quantity per output unit to a DRAFT BOM.

    - Once a BOM is ACTIVE or OBSOLETE, lines are strictly immutable.
    - Component cannot self-reference the finished-good revision.
    """
    return service.add_bom_line(session, organization_id, bom_id, payload)


@router.post("/boms/{bom_id}/activate", response_model=BOMRead, summary="Activate BOM recipe")
def activate_bom(bom_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Transitions a DRAFT BOM to ACTIVE status.

    - BOM must have at least one component line.
    - Active BOMs become the recipe authority for BOM explosion and production feasibility.
    """
    return service.activate_bom(session, organization_id, bom_id)


@router.get("/items/{item_id}", response_model=ItemRead, summary="Get item details")
def get_item(item_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Retrieves item catalog details by item ID."""
    return service.get_item(session, organization_id, item_id)


@router.get("/items/{item_id}/revisions", response_model=list[RevisionRead], summary="List item revisions")
def list_revisions(item_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Lists all revisions belonging to the specified item."""
    return service.list_revisions(session, organization_id, item_id)


@router.get("/boms/{bom_id}", response_model=BOMRead, summary="Get BOM recipe details")
def get_bom(bom_id: PathID, session: DatabaseSession, organization_id: OrganizationID):
    """Retrieves Bill of Materials recipe header and component lines by BOM ID."""
    return service.read_bom(session, organization_id, bom_id)
