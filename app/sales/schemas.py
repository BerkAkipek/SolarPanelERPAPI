"""Request and response models for sales orders and lines."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, Field

from app.production.schemas import MaterialFeasibilityItem
from app.schemas import Quantity, RecordID, RequestModel, ResponseModel, SequenceNumber

OrderStatus = Literal[
    "DRAFT", "CONFIRMED", "RESERVED", "PARTIALLY_SHIPPED", "SHIPPED", "CANCELLED"
]


class SalesOrderLineCreate(RequestModel):
    line_no: SequenceNumber | None = None
    item_revision_id: RecordID
    quantity: Quantity
    unit_price: Decimal | None = Field(default=None, ge=0, max_digits=18, decimal_places=4, allow_inf_nan=False)


class SalesOrderCreate(RequestModel):
    order_number: str = Field(min_length=1, max_length=50)
    customer_id: RecordID
    currency_code: str = Field(pattern=r"^[A-Z]{3}$")
    ordered_at: AwareDatetime | None = None
    required_at: AwareDatetime | None = None
    lines: list[SalesOrderLineCreate] = Field(default_factory=list)


class SalesOrderLineRead(ResponseModel):
    id: int
    organization_id: int
    sales_order_id: int
    line_no: int
    item_revision_id: int
    sku: str
    revision_code: str
    quantity: Decimal
    unit_price: Decimal | None


class SalesOrderRead(ResponseModel):
    id: int
    organization_id: int
    order_number: str
    customer_id: int
    status: OrderStatus
    currency_code: str
    ordered_at: datetime
    required_at: datetime | None
    created_at: datetime
    lines: list[SalesOrderLineRead]


class LineFulfillmentAnalysis(ResponseModel):
    line_id: int
    line_no: int
    item_revision_id: int
    sku: str
    revision_code: str
    ordered_quantity: Decimal
    available_quantity: Decimal
    ship_from_stock: Decimal
    production_required: Decimal
    ordered: Decimal
    available: Decimal


class FulfillmentAnalysisRead(ResponseModel):
    sales_order_id: int
    order_number: str
    status: OrderStatus
    can_fulfill_entirely_from_stock: bool
    total_ordered: Decimal
    total_from_stock: Decimal
    total_production_required: Decimal
    lines: list[LineFulfillmentAnalysis]


class SalesOrderLineFeasibilityRead(ResponseModel):
    line_id: int
    line_no: int
    item_revision_id: int
    sku: str
    revision_code: str
    ordered_quantity: Decimal
    ship_from_stock: Decimal
    production_required: Decimal
    bom_id: int | None = None
    can_produce: bool
    status_message: str | None = None
    materials: list[MaterialFeasibilityItem] = []


class SalesOrderFeasibilityRead(ResponseModel):
    sales_order_id: int
    order_number: str
    status: OrderStatus
    can_fulfill_all: bool
    can_produce: bool
    lines: list[SalesOrderLineFeasibilityRead]
