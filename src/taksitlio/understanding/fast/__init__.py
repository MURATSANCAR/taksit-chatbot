"""FAST need-understanding surface (ADR-007 §3).

The FAST layer converts a raw user utterance into a validated
``NeedProfile`` + ``ValidatedSemanticConstraints`` pair. Category codes
are never produced here — the matcher owns catalog identity. This
package exposes:

* :class:`FastNeedUnderstanding` — protocol that any implementation
  must satisfy (deterministic, remote, or hybrid).
* :class:`DeterministicFastExtractor` — rule-based Turkish extractor
  used by tests and CI when a real deployment is unavailable.
* :class:`StubRemoteFastExtractor` — placeholder for production wiring
  that raises ``FastDeploymentUnavailable`` so we never silently ship
  a fake success in the production path.
"""

from taksitlio.understanding.fast.deterministic import DeterministicFastExtractor
from taksitlio.understanding.fast.errors import (
    FastDeploymentUnavailable,
    FastExtractionError,
    NeedProfileSchemaError,
)
from taksitlio.understanding.fast.protocol import (
    FastExtractionOutcome,
    FastNeedUnderstanding,
)
from taksitlio.understanding.fast.remote import (
    RemoteFastExtractor,
    build_remote_fast_from_env,
)
from taksitlio.understanding.fast.stub_remote import StubRemoteFastExtractor

__all__ = [
    "DeterministicFastExtractor",
    "FastDeploymentUnavailable",
    "FastExtractionError",
    "FastExtractionOutcome",
    "FastNeedUnderstanding",
    "NeedProfileSchemaError",
    "RemoteFastExtractor",
    "StubRemoteFastExtractor",
    "build_remote_fast_from_env",
]
