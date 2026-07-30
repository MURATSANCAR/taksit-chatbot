"""Real-embedding challenger for the category matcher (ADR-006 §I).

The challenger explicitly rejects silent lexical fallbacks. When the
production embedding deployment is unavailable, the challenger returns a
typed ``ChallengerReport`` whose ``status`` field is
``EMBEDDING_DEPLOYMENT_UNAVAILABLE`` — never a fabricated evaluation.

The challenger is *not* an alternate matcher; it wraps an injected
``QueryEmbeddingGateway`` and reports whether it can produce query
vectors of the right dimension. The evaluation CLI compares a baseline
LEXICAL evaluation against a challenger evaluation only when the gateway
is available.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Awaitable, Callable, Optional, Sequence

from taksitlio.semantic_matching.embedding_gateway import (
    LexicalFallbackGateway,
    QueryEmbeddingGateway,
)
from taksitlio.semantic_matching.errors import EmbeddingGatewayUnavailable


class ChallengerStatus(str, Enum):
    OK = "OK"
    EMBEDDING_DEPLOYMENT_UNAVAILABLE = "EMBEDDING_DEPLOYMENT_UNAVAILABLE"
    INVALID_DIMENSION = "INVALID_DIMENSION"
    REJECTED_LEXICAL_FALLBACK = "REJECTED_LEXICAL_FALLBACK"


class ChallengerRejection(RuntimeError):
    """Raised when the challenger cannot honor its "no silent fallback" contract."""


@dataclass(frozen=True)
class ChallengerReport:
    status: ChallengerStatus
    dimension: Optional[int]
    samples_embedded: int
    reason: Optional[str] = None
    gateway_class: Optional[str] = None
    diagnostics: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "status": self.status.value,
            "dimension": self.dimension,
            "samples_embedded": self.samples_embedded,
            "reason": self.reason,
            "gateway_class": self.gateway_class,
            "diagnostics": dict(self.diagnostics),
        }


GatewayFactory = Callable[[], Awaitable[QueryEmbeddingGateway]]


def _reject_lexical(gateway: QueryEmbeddingGateway) -> Optional[ChallengerReport]:
    if isinstance(gateway, LexicalFallbackGateway):
        return ChallengerReport(
            status=ChallengerStatus.REJECTED_LEXICAL_FALLBACK,
            dimension=None,
            samples_embedded=0,
            reason=(
                "challenger refuses to run against LexicalFallbackGateway — "
                "wire a real CATEGORY_EMBEDDING gateway"
            ),
            gateway_class=type(gateway).__name__,
        )
    return None


async def probe_challenger(
    gateway: QueryEmbeddingGateway,
    *,
    samples: Sequence[str] = ("test",),
    expected_dimension: Optional[int] = None,
) -> ChallengerReport:
    """Probe *gateway* against a small set of texts.

    Returns a typed report — never silently substitutes a lexical vector.
    """

    reject = _reject_lexical(gateway)
    if reject is not None:
        return reject

    dimensions: list[int] = []
    embedded = 0
    for text in samples:
        try:
            vector = await gateway.embed_query(text)
        except EmbeddingGatewayUnavailable as exc:
            return ChallengerReport(
                status=ChallengerStatus.EMBEDDING_DEPLOYMENT_UNAVAILABLE,
                dimension=None,
                samples_embedded=embedded,
                reason=f"{exc}",
                gateway_class=type(gateway).__name__,
            )
        dimensions.append(len(vector))
        embedded += 1

    if not dimensions:
        return ChallengerReport(
            status=ChallengerStatus.EMBEDDING_DEPLOYMENT_UNAVAILABLE,
            dimension=None,
            samples_embedded=0,
            reason="no samples provided to challenger",
            gateway_class=type(gateway).__name__,
        )

    dim = dimensions[0]
    if any(d != dim for d in dimensions):
        return ChallengerReport(
            status=ChallengerStatus.INVALID_DIMENSION,
            dimension=None,
            samples_embedded=embedded,
            reason=f"inconsistent dimensions: {sorted(set(dimensions))}",
            gateway_class=type(gateway).__name__,
        )
    if expected_dimension is not None and dim != expected_dimension:
        return ChallengerReport(
            status=ChallengerStatus.INVALID_DIMENSION,
            dimension=dim,
            samples_embedded=embedded,
            reason=(
                f"expected embedding dimension {expected_dimension}, got {dim}"
            ),
            gateway_class=type(gateway).__name__,
        )
    return ChallengerReport(
        status=ChallengerStatus.OK,
        dimension=dim,
        samples_embedded=embedded,
        gateway_class=type(gateway).__name__,
    )


async def run_embedding_challenger(
    factory: GatewayFactory,
    *,
    samples: Sequence[str] = ("test",),
    expected_dimension: Optional[int] = None,
) -> ChallengerReport:
    """Instantiate a gateway via *factory* and probe it.

    Any failure to acquire the gateway is reported as
    ``EMBEDDING_DEPLOYMENT_UNAVAILABLE`` — the challenger never silently
    swaps in a LexicalEmbedder / LexicalFallbackGateway.
    """

    try:
        gateway = await factory()
    except EmbeddingGatewayUnavailable as exc:
        return ChallengerReport(
            status=ChallengerStatus.EMBEDDING_DEPLOYMENT_UNAVAILABLE,
            dimension=None,
            samples_embedded=0,
            reason=f"{exc}",
        )
    except Exception as exc:  # noqa: BLE001
        return ChallengerReport(
            status=ChallengerStatus.EMBEDDING_DEPLOYMENT_UNAVAILABLE,
            dimension=None,
            samples_embedded=0,
            reason=f"gateway factory raised: {exc!r}",
        )
    return await probe_challenger(
        gateway, samples=samples, expected_dimension=expected_dimension
    )


__all__ = [
    "ChallengerRejection",
    "ChallengerReport",
    "ChallengerStatus",
    "probe_challenger",
    "run_embedding_challenger",
]
