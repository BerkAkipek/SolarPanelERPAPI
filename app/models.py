"""Shared ORM identity and organization mapping."""
from sqlalchemy import BigInteger, Identity, String
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class IdentityRecord:
    id: Mapped[int] = mapped_column(BigInteger, Identity(), primary_key=True)


class Organization(IdentityRecord, Base):
    __tablename__ = "organizations"
    code: Mapped[str] = mapped_column(String(50))
    name: Mapped[str] = mapped_column(String(255))
