"""Validated requests and explicit public responses for product definition."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import Field

from app.schemas import Quantity, RecordID, RequestModel, ResponseModel, SequenceNumber

ItemType = Literal["MATERIAL", "COMPONENT", "FINISHED_GOOD"]
RevisionStatus = Literal["DRAFT", "ACTIVE", "OBSOLETE"]
BOMStatus = Literal["DRAFT", "ACTIVE", "OBSOLETE"]


class ItemCreate(RequestModel):
    sku: str = Field(min_length=1, max_length=100)
    name: str = Field(min_length=1, max_length=255)
    item_type: ItemType
    base_uom: str = Field(min_length=1, max_length=20)
    tracking_type: Literal["NONE", "LOT", "SERIAL"] = "NONE"


class ItemRead(ResponseModel):
    id: int
    organization_id: int
    sku: str
    name: str
    item_type: ItemType
    base_uom: str
    tracking_type: str
    is_active: bool
    created_at: datetime
    updated_at: datetime


class RevisionCreate(RequestModel):
    revision_code: str = Field(min_length=1, max_length=50)
    status: RevisionStatus = "DRAFT"


class BOMCreate(RequestModel):
    product_revision_id: RecordID
    version: SequenceNumber = 1
    output_quantity: Quantity = Decimal("1")


class BOMLineCreate(RequestModel):
    line_no: SequenceNumber
    component_revision_id: RecordID
    quantity: Quantity


class BOMSummary(ResponseModel):
    id: int
    organization_id: int
    product_revision_id: int
    version: int
    status: BOMStatus
    output_quantity: Decimal
    created_at: datetime


class RevisionRead(ResponseModel):
    id: int
    organization_id: int
    item_id: int
    revision_code: str
    status: RevisionStatus
    created_at: datetime
    boms: list[BOMSummary] = Field(default_factory=list)


class RevisionDescription(ResponseModel):
    item_id: int
    sku: str
    name: str
    base_uom: str
    revision_id: int
    revision_code: str


class BOMLineRead(ResponseModel):
    id: int
    organization_id: int
    bom_id: int
    line_no: int
    component_revision_id: int
    quantity: Decimal
    component: RevisionDescription


class BOMRead(BOMSummary):
    product: RevisionDescription
    lines: list[BOMLineRead]
