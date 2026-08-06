"""End-to-end guest smoke test against the real app (in-memory mode).

Boots create_app() with build_in_memory_container() and drives POST /v1/chat
as a guest (no user_id) through the full production route:
    chat route → run_guest_branch → GuestOrchestratorAdapter →
    CampaignOnlyGuestPipeline → in-memory CAS state + seeded campaigns.

Run:  python scripts/smoke_guest_chat.py
"""

from __future__ import annotations

import os
import sys
import uuid

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))
os.environ.setdefault("ALLOW_IN_MEMORY", "true")

from fastapi.testclient import TestClient  # noqa: E402

from taksitlio.api.app import create_app  # noqa: E402
from taksitlio.app.container import build_in_memory_container  # noqa: E402


def line(t: str) -> None:
    print("\n" + "=" * 76 + f"\n{t}\n" + "=" * 76)


def post(client, sid, message, rev=None):
    body = {"session_id": sid, "message": message}
    if rev is not None:
        body["revision"] = rev
    r = client.post("/v1/chat", json=body)
    r.raise_for_status()
    return r.json()


def show(tag, res):
    d = res.get("diagnostics") or {}
    print(f"\n>>> {tag}")
    print(f"    decision={res.get('decision')} phase={res.get('phase')} "
          f"cat={d.get('ctx_category')} budget={d.get('ctx_budget')} "
          f"src={d.get('category_source')}")
    print(f"    reply: {(res.get('reply') or '').splitlines()[0]}")
    for c in res.get("campaigns") or []:
        rate = (c.get('attributes') or {}).get('rate_text') or c.get('rate_text') or ''
        print(f"      • {c.get('title')} — {c.get('brand') or ''} ({rate})")
    if res.get("cta"):
        print(f"    CTA: {res['cta'].get('label')}")
    if res.get("chips"):
        print(f"    chips: {[c['message'] for c in res['chips']]}")
    if res.get("progress_hint"):
        print(f"    progress_hint: {res['progress_hint']}")


def main():
    app = create_app(container=build_in_memory_container())
    with TestClient(app) as client:
        h = client.get("/health")
        line(f"HEALTH {h.status_code}")

        line("1) GREETING → opening + chips + progress_hint")
        sid = str(uuid.uuid4())
        show("merhaba", post(client, sid, "merhaba"))

        line("2) CANONICAL USE CASE → phone campaigns + CTA")
        sid = str(uuid.uuid4())
        show("cep telefonu alıcaz, bütçem 40 bin TL civarı",
             post(client, sid, "cep telefonu alıcaz, bütçem 40 bin TL civarı"))

        line("3) CROSS-CATEGORY (general finance) → buzdolabı gets bank offers")
        sid = str(uuid.uuid4())
        show("buzdolabı, 30 bin TL", post(client, sid, "buzdolabı, 30 bin TL"))

        line("4) NON-DIACRITIC + budget trap")
        sid = str(uuid.uuid4())
        show("iphone 15 alicam butcem 40 bin", post(client, sid, "iphone 15 alicam butcem 40 bin"))

        line("5) IDENTITY GUARD → never reveal AI")
        sid = str(uuid.uuid4())
        show("sen yapay zeka mısın", post(client, sid, "sen yapay zeka mısın"))

        line("6) CLARIFY → budget missing")
        sid = str(uuid.uuid4())
        show("laptop bakıyorum", post(client, sid, "laptop bakıyorum"))

        line("7) TAP A STARTER CHIP")
        sid = str(uuid.uuid4())
        opening = post(client, sid, "merhaba")
        chip_msg = opening["chips"][2]["message"]  # buzdolabı chip
        show(f"chip: {chip_msg}", post(client, sid, chip_msg))

    print("\nSMOKE OK ✅")


if __name__ == "__main__":
    main()
