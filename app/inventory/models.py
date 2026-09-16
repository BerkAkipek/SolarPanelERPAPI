"""Mappings for the existing inventory ledger and its physical-balance view."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, IdentityRecord


class InventoryLocation(IdentityRecord, Base):
    __tablename__ = "inventory_locations"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(255))
    location_type: Mapped[str] = mapped_column(String(30))
    is_active: Mapped[bool] = mapped_column(Boolean)


class InventoryMovement(IdentityRecord, Base):
    __tablename__ = "inventory_movements"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    movement_type: Mapped[str] = mapped_column(String(40))
    reference: Mapped[str | None] = mapped_column(String(100))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    idempotency_key: Mapped[str | None] = mapped_column(String(100))
    request_hash: Mapped[str | None] = mapped_column(String(64))


class InventoryMovementLine(IdentityRecord, Base):
    __tablename__ = "inventory_movement_lines"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    movement_id: Mapped[int] = mapped_column(BigInteger)
    item_revision_id: Mapped[int] = mapped_column(BigInteger)
    from_location_id: Mapped[int | None] = mapped_column(BigInteger)
    to_location_id: Mapped[int | None] = mapped_column(BigInteger)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))


class InventoryOnHand(Base):
    __tablename__ = "inventory_on_hand"
    organization_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_revision_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    location_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    incoming_quantity: Mapped[Decimal] = mapped_column(Numeric)
    outgoing_quantity: Mapped[Decimal] = mapped_column(Numeric)
    on_hand_quantity: Mapped[Decimal] = mapped_column(Numeric)


class StockReservation(IdentityRecord, Base):
    __tablename__ = "stock_reservations"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    item_revision_id: Mapped[int] = mapped_column(BigInteger)
    location_id: Mapped[int] = mapped_column(BigInteger)
    sales_order_line_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    production_order_material_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    status: Mapped[str] = mapped_column(String(20), default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class InventoryAvailability(Base):
    __tablename__ = "inventory_availability"
    organization_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    item_revision_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    location_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    on_hand_quantity: Mapped[Decimal] = mapped_column(Numeric)
    reserved_quantity: Mapped[Decimal] = mapped_column(Numeric)
    available_quantity: Mapped[Decimal] = mapped_column(Numeric)

