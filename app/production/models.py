"""ORM mappings for production orders and material snapshot requirements."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, DateTime, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, IdentityRecord


class ProductionOrder(IdentityRecord, Base):
    __tablename__ = "production_orders"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    production_order_number: Mapped[str] = mapped_column(String(50))
    product_revision_id: Mapped[int] = mapped_column(BigInteger)
    bom_id: Mapped[int] = mapped_column(BigInteger)
    source_sales_order_line_id: Mapped[int | None] = mapped_column(BigInteger, default=None)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    status: Mapped[str] = mapped_column(String(30), server_default=text("'DRAFT'"))
    planned_start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class ProductionOrderMaterial(IdentityRecord, Base):
    __tablename__ = "production_order_materials"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    production_order_id: Mapped[int] = mapped_column(BigInteger)
    bom_id: Mapped[int] = mapped_column(BigInteger)
    bom_line_id: Mapped[int] = mapped_column(BigInteger)
    component_revision_id: Mapped[int] = mapped_column(BigInteger)
    required_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
