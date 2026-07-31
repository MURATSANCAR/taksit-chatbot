"""ADR-011 guest UI progress modules exist and avoid fake timers."""

from pathlib import Path

ROOT = Path(__file__).resolve().parents[3]
JS = ROOT / "web" / "taksitlio" / "js"


def test_adr011_frontend_modules_present() -> None:
    required = [
        JS / "search-session" / "client.js",
        JS / "search-progress" / "timeline.js",
        JS / "clarification" / "card.js",
        JS / "constraint-chips" / "chips.js",
        JS / "progressive-products" / "carousel.js",
        JS / "logo-progress-rail" / "rail.js",
    ]
    for path in required:
        assert path.is_file(), path
        text = path.read_text(encoding="utf-8")
        assert "setTimeout" not in text or "fake" not in text.casefold()
        assert "Bankalardan teklifler" not in text


def test_adr011_scripts_referenced_in_html() -> None:
    html = (ROOT / "web" / "taksitlio" / "index.html").read_text(encoding="utf-8")
    assert "js/search-session/client.js" in html
    assert "js/search-progress/timeline.js" in html
    assert "js/clarification/card.js" in html
