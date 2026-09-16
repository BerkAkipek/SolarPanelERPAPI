"""Shared identifier and decimal validation for ERP requests."""
from decimal import Decimal
from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

RecordID = Annotated[int, Field(gt=0, le=9223372036854775807, strict=True)]
SequenceNumber = Annotated[int, Field(gt=0, le=2147483647, strict=True)]
Quantity = Annotated[Decimal, Field(gt=0, max_digits=18, decimal_places=6, allow_inf_nan=False)]


class RequestModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class ResponseModel(BaseModel):
    model_config = ConfigDict(from_attributes=True)
