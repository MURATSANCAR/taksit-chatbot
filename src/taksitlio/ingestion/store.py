"""Ingestion source / run persistence models (ADR-010 P7)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence


@dataclass(frozen=True)
class IngestionSourceRecord:
    id: int
    merchant_id: int
    source_code: str
    source_type: str
    adapter_code: str
    status: str = "DRAFT"
    priority: int = 100
    credential_ref: Optional[str] = None
    base_url: Optional[str] = None
    consecutive_failures: int = 0
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestionRunRecord:
    id: int
    source_id: int
    run_type: str
    status: str
    items_discovered: int = 0
    items_changed: int = 0
    items_skipped: int = 0
    items_failed: int = 0
    started_at: Optional[datetime] = None
    finished_at: Optional[datetime] = None
    error_code: Optional[str] = None
    error_summary: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class IngestionRunItemRecord:
    id: int
    run_id: int
    action: str
    external_item_id: Optional[str] = None
    item_kind: str = "PRODUCT"
    content_hash: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None
    source_reference: Optional[str] = None


@dataclass(frozen=True)
class SourceHealthRecord:
    source_id: int
    health: str
    consecutive_failures: int = 0
    last_check_at: Optional[datetime] = None
    last_success_at: Optional[datetime] = None
    last_failure_at: Optional[datetime] = None
    detail: Optional[str] = None


@dataclass(frozen=True)
class CreateSourceInput:
    merchant_id: int
    source_code: str
    source_type: str
    adapter_code: str
    credential_ref: Optional[str] = None
    base_url: Optional[str] = None
    status: str = "DRAFT"
    priority: int = 100
    metadata: Mapping[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class PersistRunInput:
    source_id: int
    run_type: str
    status: str
    items_discovered: int = 0
    items_changed: int = 0
    items_skipped: int = 0
    items_failed: int = 0
    error_code: Optional[str] = None
    error_summary: Optional[str] = None
    metadata: Mapping[str, Any] = field(default_factory=dict)
    items: Sequence[Mapping[str, Any]] = ()


__all__ = [
    "CreateSourceInput",
    "IngestionRunItemRecord",
    "IngestionRunRecord",
    "IngestionSourceRecord",
    "PersistRunInput",
    "SourceHealthRecord",
]
