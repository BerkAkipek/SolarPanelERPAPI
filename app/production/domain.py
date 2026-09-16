"""Pure domain functions for production planning and BOM calculations."""
from dataclasses import dataclass
from decimal import Decimal, ROUND_HALF_UP
import math
from typing import Any, Sequence


@dataclass(frozen=True)
class MaterialRequirement:
    bom_line_id: int | None
    line_no: int
    component_revision_id: int
    quantity_per_unit: Decimal
    required_quantity: Decimal
    sku: str | None = None
    name: str | None = None
    base_uom: str | None = None


def _to_decimal(value: Any, name: str) -> Decimal:
    try:
        dec = Decimal(str(value))
    except Exception as exc:
        raise ValueError(f"{name} must be a valid decimal number, got {value!r}.") from exc
    if not dec.is_finite() or math.isnan(dec):
        raise ValueError(f"{name} must be a finite number, got {value!r}.")
    return dec


def explode_bom(
    bom: Any,
    production_quantity: Decimal | str | int | float,
    *,
    output_quantity: Decimal | str | int | float | None = None,
) -> list[MaterialRequirement]:
    """Pure domain function: explodes a BOM recipe for a target production quantity.

    Formula:
        required_quantity = round(production_quantity * line_quantity / bom_output_quantity, 6)

    No database access or side effects.
    """
    prod_qty = _to_decimal(production_quantity, "Production quantity")
    if prod_qty <= Decimal(0):
        raise ValueError(f"Production quantity must be positive, got {production_quantity}.")

    # Resolve BOM output quantity (default 1)
    if output_quantity is not None:
        bom_output_qty = _to_decimal(output_quantity, "BOM output quantity")
    elif hasattr(bom, "output_quantity") and bom.output_quantity is not None:
        bom_output_qty = _to_decimal(bom.output_quantity, "BOM output quantity")
    elif isinstance(bom, dict) and "output_quantity" in bom and bom["output_quantity"] is not None:
        bom_output_qty = _to_decimal(bom["output_quantity"], "BOM output quantity")
    else:
        bom_output_qty = Decimal(1)

    if bom_output_qty <= Decimal(0):
        raise ValueError(f"BOM output quantity must be positive, got {bom_output_qty}.")

    # Resolve BOM lines
    if hasattr(bom, "lines") and bom.lines is not None:
        raw_lines = bom.lines
    elif isinstance(bom, dict) and "lines" in bom and bom["lines"] is not None:
        raw_lines = bom["lines"]
    elif isinstance(bom, (list, tuple)):
        raw_lines = bom
    else:
        raw_lines = []

    precision = Decimal("0.000001")
    requirements: list[MaterialRequirement] = []

    for idx, line in enumerate(raw_lines, start=1):
        if isinstance(line, dict):
            line_id = line.get("id") or line.get("bom_line_id")
            line_no = line.get("line_no", idx)
            comp_rev_id = line.get("component_revision_id")
            line_qty_raw = line.get("quantity")
            sku = line.get("sku") or (line.get("component") or {}).get("sku")
            name = line.get("name") or (line.get("component") or {}).get("name")
            uom = line.get("base_uom") or (line.get("component") or {}).get("base_uom")
        else:
            line_id = getattr(line, "id", getattr(line, "bom_line_id", None))
            line_no = getattr(line, "line_no", idx)
            comp_rev_id = getattr(line, "component_revision_id", None)
            line_qty_raw = getattr(line, "quantity", None)
            comp_info = getattr(line, "component", None)
            sku = getattr(comp_info, "sku", None) if comp_info else getattr(line, "sku", None)
            name = getattr(comp_info, "name", None) if comp_info else getattr(line, "name", None)
            uom = getattr(comp_info, "base_uom", None) if comp_info else getattr(line, "base_uom", None)

        line_qty = _to_decimal(line_qty_raw, f"Line {line_no} quantity")
        if line_qty <= Decimal(0):
            raise ValueError(f"Line {line_no} quantity must be positive, got {line_qty_raw}.")

        unit_ratio = (line_qty / bom_output_qty).quantize(precision, rounding=ROUND_HALF_UP)
        raw_requirement = (prod_qty * line_qty) / bom_output_qty
        quantized_requirement = raw_requirement.quantize(precision, rounding=ROUND_HALF_UP)

        requirements.append(
            MaterialRequirement(
                bom_line_id=line_id,
                line_no=line_no,
                component_revision_id=comp_rev_id,
                quantity_per_unit=unit_ratio,
                required_quantity=quantized_requirement,
                sku=sku,
                name=name,
                base_uom=uom,
            )
        )

    return requirements


@dataclass(frozen=True)
class ComponentFeasibility:
    component_revision_id: int | None
    sku: str
    name: str | None
    base_uom: str | None
    required: Decimal
    available: Decimal
    shortage: Decimal


@dataclass(frozen=True)
class MaterialFeasibilityAnalysis:
    production_required: Decimal
    can_produce: bool
    materials: list[ComponentFeasibility]


def evaluate_material_feasibility(
    requirements: Sequence[MaterialRequirement],
    available_quantities: dict[int, Decimal | str | int | float],
    production_required: Decimal | str | int | float,
) -> MaterialFeasibilityAnalysis:
    """Pure domain function: evaluates whether inventory availability covers material requirements.

    For every component:
        required = sum(required_quantity of all BOM lines for this component)
        available = available stock in reservable locations
        shortage = max(0, required - available)
        can_produce = all(shortage == 0)

    No database access or side effects.
    """
    prod_qty = _to_decimal(production_required, "Production required")
    if prod_qty <= Decimal(0):
        raise ValueError(f"Production required quantity must be positive, got {production_required}.")

    # Aggregate requirements by component_revision_id, preserving encounter order
    comp_groups: dict[int, list[MaterialRequirement]] = {}
    for req in requirements:
        comp_groups.setdefault(req.component_revision_id, []).append(req)

    precision = Decimal("0.000001")
    materials: list[ComponentFeasibility] = []

    for comp_rev_id, lines in comp_groups.items():
        first = lines[0]
        total_required = sum((r.required_quantity for r in lines), Decimal(0)).quantize(
            precision, rounding=ROUND_HALF_UP
        )
        avail_raw = available_quantities.get(comp_rev_id, Decimal(0))
        avail = _to_decimal(avail_raw, f"Component {comp_rev_id} available stock")
        if avail < Decimal(0):
            avail = Decimal(0)
        avail = avail.quantize(precision, rounding=ROUND_HALF_UP)

        shortage = max(Decimal(0), total_required - avail).quantize(precision, rounding=ROUND_HALF_UP)

        materials.append(
            ComponentFeasibility(
                component_revision_id=comp_rev_id,
                sku=first.sku or (f"REV-{comp_rev_id}" if comp_rev_id else "UNKNOWN"),
                name=first.name,
                base_uom=first.base_uom,
                required=total_required,
                available=avail,
                shortage=shortage,
            )
        )

    can_produce = len(materials) > 0 and all(m.shortage == Decimal(0) for m in materials)

    return MaterialFeasibilityAnalysis(
        production_required=prod_qty,
        can_produce=can_produce,
        materials=materials,
    )

