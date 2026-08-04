#!/usr/bin/env python3
"""
Apply budget-before-product gate to src/taksitlio/pipeline/orchestrator.py

Usage (repo root):
  python scripts/patch_orchestrator_budget_gate.py
  python scripts/patch_orchestrator_budget_gate.py --dry-run
"""

from __future__ import annotations

import argparse
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
# When copied into real repo, parents[1] is repo root; fallback search
CANDIDATES = [
    Path("src/taksitlio/pipeline/orchestrator.py"),
    ROOT / "src/taksitlio/pipeline/orchestrator.py",
]

HELPERS = '''
    @staticmethod
    def _has_budget_cue(message: str) -> bool:
        import re
        return bool(
            re.search(
                r"(\\d+(?:[.,]\\d{3})*|\\d+)\\s*(?:bin)?\\s*(?:tl|lira|₺)"
                r"|(?:\\bbütçe|\\bbutce|\\bbütçem|\\bbutcem\\b)",
                message or "",
                re.IGNORECASE,
            )
        )

    @staticmethod
    def _need_profile_has_budget(need_profile: dict | None) -> bool:
        if not need_profile:
            return False
        budget = need_profile.get("budget") or {}
        if not isinstance(budget, dict):
            return False
        for key in ("value", "maximum", "monthly_payment"):
            raw = budget.get(key)
            if raw is None:
                continue
            try:
                if float(raw) > 0:
                    return True
            except (TypeError, ValueError):
                continue
        return False
'''

# Pattern for early product short-circuit
EARLY_OLD = re.compile(
    r"if \(\s*"
    r"request\.prefer_search_sessions\s*"
    r"and self\._search_orchestrator is not None\s*"
    r"and not request\.product_phase\s*"
    r"and self\._looks_like_product_query\(request\.message\)\s*"
    r"\):",
    re.MULTILINE,
)

EARLY_NEW = """if (
            self._search_orchestrator is not None
            and request.prefer_search_sessions
            and self._looks_like_product_query(request.message)
            and (
                # Explicit progressive phase from client → allow product browse
                bool(request.product_phase)
                # Or budget present in utterance → product+finance search OK
                or self._has_budget_cue(request.message)
            )
        ):"""

PRODUCT_OLD = re.compile(
    r"# Prefer ADR-010 catalog path when products exist; else legacy V004 campaigns\.\s*"
    r"product_hit = await self\._try_product_path\(request, need_profile\)\s*"
    r"if product_hit is not None:",
    re.MULTILINE,
)

PRODUCT_NEW = """# Conversion-first: without budget do NOT dump product catalog.
        # Guest/loginsiz funnel needs budget clarify → campaigns + CTA.
        # Explicit product_phase=FIRST_CARDS still allows browse.
        _phase = (request.product_phase or "").upper()
        _allow_products = _phase == "FIRST_CARDS" or self._need_profile_has_budget(
            need_profile
        )
        if not _allow_products:
            reply = await self._responder.clarify("budget")
            return self._build(
                request,
                turn,
                reply,
                match_result.matches,
                [],
                started,
                phase="CLARIFY",
                extra={
                    "reason": "budget_required_before_products",
                    "product_path": False,
                    "category_codes": category_codes,
                },
            )

        # Prefer ADR-010 catalog path when products exist; else legacy V004 campaigns.
        product_hit = await self._try_product_path(request, need_profile)
        if product_hit is not None:"""


def find_orchestrator() -> Path:
    for p in CANDIDATES:
        if p.exists():
            return p
    raise SystemExit(
        "orchestrator.py not found. Run from repo root or pass --path"
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--path", type=Path, default=None)
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()
    path = args.path or find_orchestrator()
    text = path.read_text(encoding="utf-8")
    original = text

    if "_has_budget_cue" not in text:
        # Insert helpers before _looks_like_product_query
        marker = "    @staticmethod\n    def _looks_like_product_query"
        if marker not in text:
            print("Could not find _looks_like_product_query to insert helpers")
            return 1
        text = text.replace(marker, HELPERS + "\n" + marker, 1)
        print("+ inserted _has_budget_cue / _need_profile_has_budget")

    if EARLY_OLD.search(text):
        text = EARLY_OLD.sub(EARLY_NEW, text, count=1)
        print("+ patched early product search gate")
    else:
        print("! early block pattern not found (already patched?)")

    if PRODUCT_OLD.search(text):
        text = PRODUCT_OLD.sub(PRODUCT_NEW, text, count=1)
        print("+ patched _try_product_path budget gate")
    else:
        print("! product_hit block pattern not found (already patched?)")

    if text == original:
        print("No changes")
        return 0
    if args.dry_run:
        print("DRY-RUN OK — would write", path)
        return 0
    path.write_text(text, encoding="utf-8")
    print("Wrote", path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
