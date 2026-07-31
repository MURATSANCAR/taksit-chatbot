"""Runtime integration smoke — typed blocked dependency when models absent."""

from __future__ import annotations

import pytest

from taksitlio.runtime_verification.probes import probe_all_dependencies
from taksitlio.runtime_verification.gate import (
    RuntimeGateStatus,
    evaluate_runtime_gate,
)


pytestmark = pytest.mark.integration


def test_probe_all_never_silently_succeeds_without_urls(monkeypatch):
    monkeypatch.delenv("FAST_PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("POC_FAST_BASE_URL", raising=False)
    monkeypatch.delenv("EMBEDDING_PROVIDER_BASE_URL", raising=False)
    monkeypatch.delenv("POC_EMBEDDING_BASE_URL", raising=False)
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("PGVECTOR_URL", raising=False)
    monkeypatch.delenv("DATABASE_URL", raising=False)
    report = probe_all_dependencies(
        redis_url=None,
        postgres_url=None,
        fast_base_url=None,
        embedding_base_url=None,
    )
    assert report.fast.available is False
    assert report.embedding.available is False
    assert report.redis.available is False
    assert evaluate_runtime_gate(report) == RuntimeGateStatus.BLOCKED_DEPENDENCY
