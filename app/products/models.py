"""ORM mappings for the product-definition tables; Alembic owns schema changes."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, IdentityRecord


class Item(IdentityRecord, Base):
    __tablename__ = "items"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    sku: Mapped[str] = mapped_column(String(100))
    name: Mapped[str] = mapped_column(String(255))
    item_type: Mapped[str] = mapped_column(String(30))
    base_uom: Mapped[str] = mapped_column(String(20))
    tracking_type: Mapped[str] = mapped_column(String(20))
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class ItemRevision(IdentityRecord, Base):
    __tablename__ = "item_revisions"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    item_id: Mapped[int] = mapped_column(BigInteger)
    revision_code: Mapped[str] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class BOM(IdentityRecord, Base):
    __tablename__ = "boms"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    product_revision_id: Mapped[int] = mapped_column(BigInteger)
    version: Mapped[int] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(20), server_default=text("'DRAFT'"))
    output_quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class BOMLine(IdentityRecord, Base):
    __tablename__ = "bom_lines"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    bom_id: Mapped[int] = mapped_column(BigInteger)
    line_no: Mapped[int] = mapped_column(Integer)
    component_revision_id: Mapped[int] = mapped_column(BigInteger)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
