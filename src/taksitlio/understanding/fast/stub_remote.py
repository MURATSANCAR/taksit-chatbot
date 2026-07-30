"""StubRemoteFastExtractor — no silent success in production path (ADR-007 §3).

If the wiring code accidentally uses this stub in production, every
``extract()`` call raises ``FastDeploymentUnavailable`` so the caller
must handle BLOCKED_DEPENDENCY explicitly. This prevents a well-meaning
default from silently substituting a lexical or rule-based extractor
for the real FAST model deployment.
"""

from __future__ import annotations

from typing import Any, Mapping, Optional

from taksitlio.understanding.fast.errors import FastDeploymentUnavailable
from taksitlio.understanding.fast.protocol import FastExtractionOutcome


class StubRemoteFastExtractor:
    """Placeholder for the real FAST model deployment.

    Instantiate this class in production wiring only when the real
    deployment address is not configured yet — it will loudly refuse to
    produce a NeedProfile until it is replaced with an actual client.
    """

    name = "stub-remote-fast-extractor"

    def __init__(self, *, reason: str = "no FAST model configured") -> None:
        self._reason = reason

    async def extract(
        self,
        utterance: str,
        *,
        locale: str = "tr-TR",
        session_summary: Optional[Mapping[str, Any]] = None,
    ) -> FastExtractionOutcome:
        raise FastDeploymentUnavailable(
            f"FAST deployment unavailable: {self._reason}"
        )


__all__ = ["StubRemoteFastExtractor"]
