"""Numeric attribute extraction safety — prevent cross-dimension misreads."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional


class AttributeDimension(str, Enum):
    MEMORY = "MEMORY"
    STORAGE = "STORAGE"
    DISPLAY_SIZE = "DISPLAY_SIZE"
    MASS = "MASS"
    LENGTH = "LENGTH"
    UNKNOWN = "UNKNOWN"


_UNIT_DIMENSION: Mapping[str, AttributeDimension] = {
    "gb": AttributeDimension.STORAGE,  # ambiguous; disambiguated by attribute context
    "mb": AttributeDimension.STORAGE,
    "tb": AttributeDimension.STORAGE,
    "ram_gb": AttributeDimension.MEMORY,
    "inch": AttributeDimension.DISPLAY_SIZE,
    "in": AttributeDimension.DISPLAY_SIZE,
    '"': AttributeDimension.DISPLAY_SIZE,
    "kg": AttributeDimension.MASS,
    "g": AttributeDimension.MASS,
    "cm": AttributeDimension.LENGTH,
    "mm": AttributeDimension.LENGTH,
    "m": AttributeDimension.LENGTH,
}


_ATTRIBUTE_DIMENSION: Mapping[str, AttributeDimension] = {
    "ram": AttributeDimension.MEMORY,
    "memory": AttributeDimension.MEMORY,
    "storage": AttributeDimension.STORAGE,
    "disk": AttributeDimension.STORAGE,
    "ssd": AttributeDimension.STORAGE,
    "hdd": AttributeDimension.STORAGE,
    "screen_size": AttributeDimension.DISPLAY_SIZE,
    "display_size": AttributeDimension.DISPLAY_SIZE,
    "weight": AttributeDimension.MASS,
    "mass": AttributeDimension.MASS,
}


@dataclass(frozen=True)
class NumericExtraction:
    attribute_code: str
    raw_value: str
    normalized_value: Optional[float]
    unit_code: Optional[str]
    confidence: float
    evidence_span: str
    extractor_version: str = "v1"
    source: str = "STRUCTURED"


@dataclass(frozen=True)
class NumericValidationResult:
    accepted: bool
    usable_in_required_filter: bool
    reasons: tuple[str, ...]
    dimension: AttributeDimension


def dimension_for_unit(unit_code: Optional[str]) -> AttributeDimension:
    if not unit_code:
        return AttributeDimension.UNKNOWN
    return _UNIT_DIMENSION.get(unit_code.lower().strip(), AttributeDimension.UNKNOWN)


def dimension_for_attribute(attribute_code: str) -> AttributeDimension:
    return _ATTRIBUTE_DIMENSION.get(attribute_code.lower().strip(), AttributeDimension.UNKNOWN)


def validate_numeric_extraction(
    extraction: NumericExtraction,
    *,
    minimum_confidence_for_required_filter: float = 0.95,
    require_unit_context: bool = True,
    reject_cross_dimension: bool = True,
) -> NumericValidationResult:
    reasons: list[str] = []
    attr_dim = dimension_for_attribute(extraction.attribute_code)
    unit_dim = dimension_for_unit(extraction.unit_code)

    if extraction.normalized_value is None:
        reasons.append("missing_normalized_value")
    if require_unit_context and not extraction.unit_code:
        reasons.append("missing_unit")
    if (
        reject_cross_dimension
        and attr_dim is not AttributeDimension.UNKNOWN
        and unit_dim is not AttributeDimension.UNKNOWN
        and attr_dim != unit_dim
        # GB may be MEMORY or STORAGE; allow when attribute says MEMORY and unit is gb
        and not (
            attr_dim is AttributeDimension.MEMORY
            and extraction.unit_code
            and extraction.unit_code.lower() in {"gb", "mb"}
        )
        and not (
            attr_dim is AttributeDimension.STORAGE
            and extraction.unit_code
            and extraction.unit_code.lower() in {"gb", "mb", "tb"}
        )
    ):
        reasons.append("cross_dimension_mismatch")

    # Classic failure modes
    raw = extraction.raw_value.lower().replace(",", ".")
    if "inç" in raw or "inch" in raw or '"' in raw:
        if attr_dim not in {AttributeDimension.DISPLAY_SIZE, AttributeDimension.UNKNOWN}:
            reasons.append("inch_used_outside_display_size")
    if "kg" in raw and extraction.normalized_value is not None:
        # "1,8 kg" must not become 18
        if "," in extraction.raw_value or "." in extraction.raw_value:
            # if someone stripped decimal separator incorrectly
            if extraction.unit_code == "kg" and extraction.normalized_value >= 10:
                # heuristic: consumer product weight rarely jumps an order from "1,8"
                if any(tok in extraction.raw_value for tok in ("1,", "1.", "2,", "2.")):
                    if extraction.normalized_value >= 10:
                        reasons.append("decimal_separator_corruption_suspected")

    accepted = "cross_dimension_mismatch" not in reasons and "missing_normalized_value" not in reasons
    if "inch_used_outside_display_size" in reasons:
        accepted = False
    if "decimal_separator_corruption_suspected" in reasons:
        accepted = False

    usable = (
        accepted
        and extraction.confidence >= minimum_confidence_for_required_filter
        and "missing_unit" not in reasons
    )
    return NumericValidationResult(
        accepted=accepted,
        usable_in_required_filter=usable,
        reasons=tuple(reasons),
        dimension=attr_dim if attr_dim is not AttributeDimension.UNKNOWN else unit_dim,
    )


def disambiguate_gb_unit(*, attribute_code: str, unit_code: str) -> str:
    """Map bare GB to ram_gb vs storage gb using attribute context."""

    dim = dimension_for_attribute(attribute_code)
    u = unit_code.lower()
    if u in {"gb", "mb"} and dim is AttributeDimension.MEMORY:
        return f"ram_{u}"
    return u


__all__ = [
    "AttributeDimension",
    "NumericExtraction",
    "NumericValidationResult",
    "dimension_for_attribute",
    "dimension_for_unit",
    "disambiguate_gb_unit",
    "validate_numeric_extraction",
]
