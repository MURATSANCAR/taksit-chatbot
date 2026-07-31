"""Resolve credential_ref values without storing secrets in DB (ADR-010 P15).

Supported schemes:
- ``env://VAR_NAME`` — read from process environment
- ``bearer:env://VAR_NAME`` — Authorization: Bearer <env value>
- ``header:Name:env://VAR_NAME`` — arbitrary header from env

Inline secrets in adapter config remain forbidden at the admin API layer.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Mapping, Optional


class CredentialResolveError(ValueError):
    """Raised when credential_ref cannot be resolved."""


@dataclass(frozen=True)
class ResolvedCredential:
    """Opaque resolved material for HTTP adapters (never log ``value``)."""

    scheme: str
    headers: Mapping[str, str]
    # Raw token only when needed by non-header auth; prefer headers.
    value: Optional[str] = None


def resolve_credential_ref(ref: Optional[str]) -> Optional[ResolvedCredential]:
    """Parse and resolve ``credential_ref``. Returns None when ref is empty."""

    if ref is None:
        return None
    text = str(ref).strip()
    if not text:
        return None

    if text.startswith("header:") and ":env://" in text:
        # header:X-Api-Key:env://VAR
        rest = text[len("header:") :]
        name, _, env_part = rest.partition(":")
        if not name or not env_part.startswith("env://"):
            raise CredentialResolveError(f"invalid header credential_ref: {ref!r}")
        value = _env_value(env_part[len("env://") :])
        return ResolvedCredential(
            scheme="header",
            headers={name.strip(): value},
            value=value,
        )

    if text.startswith("bearer:env://"):
        value = _env_value(text[len("bearer:env://") :])
        return ResolvedCredential(
            scheme="bearer",
            headers={"Authorization": f"Bearer {value}"},
            value=value,
        )

    if text.startswith("env://"):
        value = _env_value(text[len("env://") :])
        # Default: treat as bearer token (common feed auth).
        return ResolvedCredential(
            scheme="env",
            headers={"Authorization": f"Bearer {value}"},
            value=value,
        )

    raise CredentialResolveError(
        "unsupported credential_ref scheme; use env://, bearer:env://, or header:Name:env://"
    )


def http_headers_from_credential_ref(ref: Optional[str]) -> dict[str, str]:
    """Convenience: headers only (empty dict when ref is None)."""

    resolved = resolve_credential_ref(ref)
    if resolved is None:
        return {}
    return dict(resolved.headers)


def _env_value(var_name: str) -> str:
    name = (var_name or "").strip()
    if not name or not name.replace("_", "").isalnum():
        raise CredentialResolveError(f"invalid env var name in credential_ref: {var_name!r}")
    value = os.environ.get(name)
    if value is None or value == "":
        raise CredentialResolveError(f"environment variable not set: {name}")
    return value


__all__ = [
    "CredentialResolveError",
    "ResolvedCredential",
    "http_headers_from_credential_ref",
    "resolve_credential_ref",
]
