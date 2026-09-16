"""ORM mappings for sales orders, lines, and customer partners."""
from datetime import datetime
from decimal import Decimal

from sqlalchemy import BigInteger, Boolean, DateTime, Integer, Numeric, String, text
from sqlalchemy.orm import Mapped, mapped_column

from app.models import Base, IdentityRecord


class BusinessPartner(IdentityRecord, Base):
    __tablename__ = "business_partners"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(255))
    tax_number: Mapped[str | None] = mapped_column(String(50), default=None)
    email: Mapped[str | None] = mapped_column(String(255), default=None)
    phone: Mapped[str | None] = mapped_column(String(50), default=None)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class SalesOrder(IdentityRecord, Base):
    __tablename__ = "sales_orders"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    order_number: Mapped[str] = mapped_column(String(50))
    customer_id: Mapped[int] = mapped_column(BigInteger)
    status: Mapped[str] = mapped_column(String(30), default="DRAFT")
    currency_code: Mapped[str] = mapped_column(String(3))
    ordered_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))
    required_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), default=None)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=text("now()"))


class SalesOrderLine(IdentityRecord, Base):
    __tablename__ = "sales_order_lines"
    organization_id: Mapped[int] = mapped_column(BigInteger)
    sales_order_id: Mapped[int] = mapped_column(BigInteger)
    line_no: Mapped[int] = mapped_column(Integer)
    item_revision_id: Mapped[int] = mapped_column(BigInteger)
    quantity: Mapped[Decimal] = mapped_column(Numeric(18, 6))
    unit_price: Mapped[Decimal | None] = mapped_column(Numeric(18, 4), default=None)
