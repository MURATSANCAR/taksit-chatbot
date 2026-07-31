"""Merchant domain types (ADR-010 §34 / §74)."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Mapping, Optional


class MerchantActivationGate(str, Enum):
    READY = "READY"
    PARTIAL = "PARTIAL"
    BLOCKED = "BLOCKED"


@dataclass(frozen=True)
class MerchantRecord:
    merchant_code: str
    display_name: str
    status: str = "ACTIVE"
    activation_gate: MerchantActivationGate = MerchantActivationGate.BLOCKED
    canonical_name: Optional[str] = None
    normalized_name: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class MerchantLocationRecord:
    merchant_code: str
    location_code: str
    display_name: str
    city: Optional[str] = None
    district: Optional[str] = None
    address_line: Optional[str] = None
    status: str = "ACTIVE"


__all__ = [
    "MerchantActivationGate",
    "MerchantLocationRecord",
    "MerchantRecord",
]
