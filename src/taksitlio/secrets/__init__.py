"""Secrets resolution package (credential_ref → runtime material)."""

from taksitlio.secrets.resolve import (
    CredentialResolveError,
    ResolvedCredential,
    http_headers_from_credential_ref,
    resolve_credential_ref,
)

__all__ = [
    "CredentialResolveError",
    "ResolvedCredential",
    "http_headers_from_credential_ref",
    "resolve_credential_ref",
]
