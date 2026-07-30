"""Vector helpers for semantic matching (no heavy ML deps required)."""

from __future__ import annotations

import math
from typing import Sequence


def cosine_similarity(a: Sequence[float], b: Sequence[float]) -> float:
    if len(a) != len(b) or not a:
        return 0.0
    dot = 0.0
    norm_a = 0.0
    norm_b = 0.0
    for x, y in zip(a, b):
        dot += x * y
        norm_a += x * x
        norm_b += y * y
    if norm_a <= 0.0 or norm_b <= 0.0:
        return 0.0
    return dot / (math.sqrt(norm_a) * math.sqrt(norm_b))


def l2_normalize(vector: Sequence[float]) -> list[float]:
    norm = math.sqrt(sum(x * x for x in vector))
    if norm <= 0.0:
        return [0.0 for _ in vector]
    return [x / norm for x in vector]


def bag_of_chars_embedding(text: str, *, dim: int = 256) -> list[float]:
    """
    Deterministic lexical embedding for offline / test / bootstrap matching.

    Production semantic matching should use the EMBEDDING model profile.
    This fallback keeps the pipeline runnable without an embedding server.
    """
    vec = [0.0] * dim
    normalized = text.casefold()
    for i, ch in enumerate(normalized):
        idx = (ord(ch) + i * 31) % dim
        vec[idx] += 1.0
    # bigrams
    for i in range(len(normalized) - 1):
        idx = (ord(normalized[i]) * 257 + ord(normalized[i + 1])) % dim
        vec[idx] += 0.5
    return l2_normalize(vec)
