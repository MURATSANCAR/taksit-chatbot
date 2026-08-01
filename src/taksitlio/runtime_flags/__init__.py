"""Runtime feature flags (P2-LIVE activation). Statuses from DB/policy — not hardcoding."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Mapping, Optional


class FeatureFlagStatus(str, Enum):
    DISABLED = "DISABLED"
    SHADOW = "SHADOW"
    INTERNAL = "INTERNAL"
    ENABLED = "ENABLED"


# Seed defaults match activation plan; production must load from runtime_feature_flags.
_SEED: Mapping[str, FeatureFlagStatus] = {
    "adaptive_catalog_enabled": FeatureFlagStatus.ENABLED,
    "learning_candidate_generation_enabled": FeatureFlagStatus.ENABLED,
    "learning_auto_promotion_enabled": FeatureFlagStatus.DISABLED,
    "dynamic_readiness_enabled": FeatureFlagStatus.SHADOW,
    "adaptive_ranking_enabled": FeatureFlagStatus.SHADOW,
    "rolling_golden_enabled": FeatureFlagStatus.ENABLED,
}


@dataclass(frozen=True)
class FeatureFlag:
    flag_code: str
    status: FeatureFlagStatus
    config: Mapping[str, object]


def seed_flags() -> dict[str, FeatureFlag]:
    return {
        code: FeatureFlag(flag_code=code, status=status, config={})
        for code, status in _SEED.items()
    }


def flags_from_rows(rows: list[Mapping[str, object]]) -> dict[str, FeatureFlag]:
    out = seed_flags()
    for row in rows:
        code = str(row["flag_code"])
        status = FeatureFlagStatus(str(row["status"]))
        cfg = row.get("config") or {}
        if not isinstance(cfg, Mapping):
            cfg = {}
        out[code] = FeatureFlag(flag_code=code, status=status, config=dict(cfg))
    return out


def is_enabled(flags: Mapping[str, FeatureFlag], code: str) -> bool:
    f = flags.get(code)
    return f is not None and f.status is FeatureFlagStatus.ENABLED


def is_shadow_or_enabled(flags: Mapping[str, FeatureFlag], code: str) -> bool:
    f = flags.get(code)
    return f is not None and f.status in {
        FeatureFlagStatus.SHADOW,
        FeatureFlagStatus.INTERNAL,
        FeatureFlagStatus.ENABLED,
    }


def is_internal_or_enabled(flags: Mapping[str, FeatureFlag], code: str) -> bool:
    f = flags.get(code)
    return f is not None and f.status in {
        FeatureFlagStatus.INTERNAL,
        FeatureFlagStatus.ENABLED,
    }


def auto_promotion_allowed(flags: Mapping[str, FeatureFlag]) -> bool:
    """Hard safety: never treat missing flag as enabled."""

    f = flags.get("learning_auto_promotion_enabled")
    return f is not None and f.status is FeatureFlagStatus.ENABLED


__all__ = [
    "FeatureFlag",
    "FeatureFlagStatus",
    "auto_promotion_allowed",
    "flags_from_rows",
    "is_enabled",
    "is_internal_or_enabled",
    "is_shadow_or_enabled",
    "seed_flags",
]
