"""Physical ledger requests and responses. Quantities use the item's base UOM."""
from datetime import datetime
from decimal import Decimal
from typing import Literal

from pydantic import AwareDatetime, Field, model_validator

from app.schemas import Quantity, RecordID, RequestModel, ResponseModel

MovementType = Literal["RECEIPT", "TRANSFER", "ADJUSTMENT_IN", "ADJUSTMENT_OUT"]


class MovementLineCreate(RequestModel):
    item_revision_id: RecordID
    from_location_id: RecordID | None = None
    to_location_id: RecordID | None = None
    quantity: Quantity

    @model_validator(mode="after")
    def distinct_locations(self):
        if self.from_location_id is not None and self.from_location_id == self.to_location_id:
            raise ValueError("Source and destination locations must differ.")
        return self


class MovementCreate(RequestModel):
    movement_type: MovementType
    reference: str | None = Field(default=None, max_length=100)
    occurred_at: AwareDatetime | None = None
    lines: list[MovementLineCreate] = Field(min_length=1)


class MovementLineRead(ResponseModel):
    id: int
    organization_id: int
    movement_id: int
    item_revision_id: int
    from_location_id: int | None
    to_location_id: int | None
    quantity: Decimal


class MovementRead(ResponseModel):
    id: int
    organization_id: int
    movement_type: MovementType
    reference: str | None
    occurred_at: datetime
    created_at: datetime
    lines: list[MovementLineRead]


class LocationBalance(ResponseModel):
    location_id: int
    code: str
    name: str
    incoming_quantity: Decimal
    outgoing_quantity: Decimal
    on_hand_quantity: Decimal


class OnHandRead(ResponseModel):
    organization_id: int
    item_revision_id: int
    item_id: int
    sku: str
    revision_code: str
    base_uom: str
    incoming_quantity: Decimal
    outgoing_quantity: Decimal
    on_hand_quantity: Decimal
    locations: list[LocationBalance]


ReservationStatus = Literal["ACTIVE", "CONSUMED", "RELEASED"]


class ReservationCreate(RequestModel):
    item_revision_id: RecordID
    location_id: RecordID
    quantity: Quantity
    sales_order_line_id: RecordID | None = None
    production_order_material_id: RecordID | None = None

    @model_validator(mode="after")
    def exactly_one_owner(self):
        non_nulls = sum(
            1 for x in (self.sales_order_line_id, self.production_order_material_id) if x is not None
        )
        if non_nulls != 1:
            raise ValueError(
                "Reservation requires exactly one owner: sales_order_line_id or production_order_material_id."
            )
        return self


class ReservationRead(ResponseModel):
    id: int
    organization_id: int
    item_revision_id: int
    location_id: int
    sales_order_line_id: int | None
    production_order_material_id: int | None
    quantity: Decimal
    status: ReservationStatus
    created_at: datetime
    updated_at: datetime


class LocationAvailability(ResponseModel):
    location_id: int
    code: str
    name: str
    on_hand: Decimal
    reserved: Decimal
    available: Decimal


class AvailabilityRead(ResponseModel):
    organization_id: int
    item_revision_id: int
    item_id: int
    sku: str
    revision_code: str
    base_uom: str
    on_hand: Decimal
    reserved: Decimal
    available: Decimal
    locations: list[LocationAvailability]

