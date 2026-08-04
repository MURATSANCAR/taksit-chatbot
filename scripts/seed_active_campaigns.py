#!/usr/bin/env python3
"""Convenience runner for the Excel → campaign catalog seed.

Example:
    python scripts/seed_active_campaigns.py \\
        --excel /path/to/Kategoriler\\ ve\\ Kampanya\\ Örnekleri.xlsx \\
        --dry-run -v
"""

from __future__ import annotations

import sys
from pathlib import Path

# Allow running from repo root without installing the package first
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from taksitlio.campaign.seed_from_excel import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
