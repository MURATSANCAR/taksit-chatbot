"""Guest membership CTA — no rate invent."""

from __future__ import annotations

import os

from taksitlio.search_sessions.membership_cta import (
    DEFAULT_BODY,
    DEFAULT_LABEL,
    attach_guest_membership_cta,
    build_guest_membership_cta,
)


def test_cta_absent_without_products() -> None:
    assert build_guest_membership_cta(product_count=0) is None


def test_cta_label_url_only_no_finance_claims() -> None:
    cta = build_guest_membership_cta(
        product_count=2, membership_url="https://taksitlio.example/uye-ol"
    )
    assert cta is not None
    assert cta["enabled"] is True
    assert cta["label"] == DEFAULT_LABEL
    assert cta["url"] == "https://taksitlio.example/uye-ol"
    assert cta["body"] == DEFAULT_BODY
    blob = f"{cta['label']} {cta['body']}"
    assert "%" not in blob
    assert "faiz" not in blob.casefold()


def test_cta_url_from_env(monkeypatch) -> None:
    monkeypatch.setenv("TAKSITLIO_MEMBERSHIP_CTA_URL", "https://app.taksitlio.com/register")
    cta = build_guest_membership_cta(product_count=1)
    assert cta is not None
    assert cta["url"] == "https://app.taksitlio.com/register"
    monkeypatch.delenv("TAKSITLIO_MEMBERSHIP_CTA_URL", raising=False)
    assert "TAKSITLIO_MEMBERSHIP_CTA_URL" not in os.environ


def test_attach_to_payload_with_products() -> None:
    payload = {"results": {"products": [{"id": "1"}, {"id": "2"}]}}
    attach_guest_membership_cta(
        payload, membership_url="https://taksitlio.example/uye-ol"
    )
    assert payload["cta"]["enabled"] is True
    assert payload["cta"]["url"] == "https://taksitlio.example/uye-ol"


def test_attach_skips_empty_results() -> None:
    payload = {"results": {"products": []}}
    attach_guest_membership_cta(payload)
    assert "cta" not in payload
