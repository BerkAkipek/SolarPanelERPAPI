from datetime import datetime
from decimal import Decimal

from pydantic import AwareDatetime, Field

from app.schemas import Quantity, RecordID, RequestModel, ResponseModel


class ExplodedRequirementRead(ResponseModel):
    bom_line_id: int | None
    line_no: int
    component_revision_id: int
    quantity_per_unit: Decimal
    required_quantity: Decimal
    sku: str | None = None
    name: str | None = None
    base_uom: str | None = None


class BOMExplosionRead(ResponseModel):
    bom_id: int
    production_quantity: Decimal
    requirements: list[ExplodedRequirementRead]


class MaterialFeasibilityItem(ResponseModel):
    component_revision_id: int | None = None
    sku: str
    name: str | None = None
    base_uom: str | None = None
    required: Decimal
    available: Decimal
    shortage: Decimal


class MaterialFeasibilityRead(ResponseModel):
    bom_id: int | None = None
    production_required: Decimal
    can_produce: bool
    materials: list[MaterialFeasibilityItem]


class ProductionOrderCreate(RequestModel):
    production_order_number: str | None = Field(default=None, min_length=1, max_length=50)
    source_sales_order_line_id: RecordID | None = None
    product_revision_id: RecordID | None = None
    bom_id: RecordID | None = None
    quantity: Quantity
    planned_start_at: AwareDatetime | None = None


class ProductionOrderRelease(RequestModel):
    location_id: RecordID | None = Field(
        default=None,
        description="Optional preferred stock location to reserve components from.",
    )


class ProductionOrderComplete(RequestModel):
    to_location_id: RecordID | None = Field(
        default=None,
        description="Location to receive produced finished goods. If omitted, uses default active warehouse.",
    )
    occurred_at: datetime | None = None
    idempotency_key: str | None = Field(default=None, max_length=100)


class ProductionOrderMaterialRead(ResponseModel):
    id: int
    bom_id: int
    bom_line_id: int
    component_revision_id: int
    sku: str | None = None
    name: str | None = None
    base_uom: str | None = None
    required_quantity: Decimal
    reserved_quantity: Decimal = Decimal(0)
    consumed_quantity: Decimal = Decimal(0)



class ProductionOrderRead(ResponseModel):
    id: int
    organization_id: int
    production_order_number: str
    product_revision_id: int
    sku: str | None = None
    revision_code: str | None = None
    bom_id: int
    bom_version: int | None = None
    source_sales_order_line_id: int | None = None
    quantity: Decimal
    status: str
    planned_start_at: datetime | None = None
    created_at: datetime
    materials: list[ProductionOrderMaterialRead] = []

