"""Guest extraction latency + prompt inspector.

Shows exactly what is sent to the small model and measures timing:
  * deterministic path (sub-ms, always) over N utterances,
  * LLM fallback plumbing latency (offline mock, or the real endpoint when
    GUEST_FAST_BASE_URL / GUEST_FAST_MODEL are set).

Run:
    python scripts/bench_guest_llm.py            # offline mock + deterministic
    GUEST_FAST_BASE_URL=http://127.0.0.1:8000 GUEST_FAST_MODEL=small-fast \
        python scripts/bench_guest_llm.py --live 50
"""

from __future__ import annotations

import asyncio
import json
import os
import statistics
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from taksitlio.guest.llm_fallback import GuestCategoryResolverLLM  # noqa: E402
from taksitlio.guest.need_extraction import parse_budget, resolve_category  # noqa: E402

SAMPLE = "kızıma doğum günü için akordeon alıcam bütçem 30 bin civarı"


def show_prompt() -> None:
    print("=" * 72)
    print("SMALL-MODEL REQUEST (what goes to the model)")
    print("=" * 72)
    llm = GuestCategoryResolverLLM(base_url="http://placeholder", model_reference="small-fast")
    body = llm.request_body(SAMPLE)
    print(f"model         : {body['model']}")
    print(f"temperature   : {body['temperature']}   max_tokens: {body['max_tokens']}")
    print(f"response_format: {body['response_format']}")
    for msg in body["messages"]:
        print(f"\n[{msg['role'].upper()}]\n{msg['content']}")
    print()


def bench_deterministic(n: int = 20000) -> None:
    print("=" * 72)
    print(f"DETERMINISTIC PATH  (n={n})")
    print("=" * 72)
    utts = [
        "cep telefonu 40 bin", "buzdolabı 30 bin tl", "iphone 15 alıcam 40 bin",
        "akıllı saat 8 bin", "telefon kılıfı 500 tl", "yarım milyon telefon",
    ]
    t0 = time.perf_counter()
    for i in range(n):
        u = utts[i % len(utts)]
        resolve_category(u)
        parse_budget(u)
    dt = time.perf_counter() - t0
    print(f"total {dt*1000:.1f} ms  |  per-utterance {dt/n*1e6:.1f} µs  "
          f"({int(n/dt):,} utt/s)\n")


async def bench_llm(live: int) -> None:
    print("=" * 72)
    print("LLM FALLBACK PATH")
    print("=" * 72)
    if live and os.environ.get("GUEST_FAST_BASE_URL") and os.environ.get("GUEST_FAST_MODEL"):
        llm = GuestCategoryResolverLLM.from_env()
        print(f"LIVE endpoint, {live} calls…")
        lat = []
        for _ in range(live):
            await llm.resolve(SAMPLE + f" #{_}")  # vary to avoid cache
            if llm.last_latency_ms is not None:
                lat.append(llm.last_latency_ms)
        await llm.aclose()
        if lat:
            lat.sort()
            print(f"n={len(lat)}  p50={statistics.median(lat):.0f}ms  "
                  f"p95={lat[int(len(lat)*0.95)-1]:.0f}ms  "
                  f"min={lat[0]:.0f}  max={lat[-1]:.0f}")
        return

    # Offline: httpx MockTransport returns a canned classification instantly,
    # so this measures our plumbing overhead only (real model adds its own time).
    import httpx

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"category_code":"2"}'}}]
        })

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    llm = GuestCategoryResolverLLM(base_url="http://mock", model_reference="small-fast", client=client)
    hit = await llm.resolve(SAMPLE)
    print(f"offline mock → category={hit.category_code if hit else None} "
          f"({hit.display_name if hit else '-'})")
    print(f"plumbing latency (mock, no model compute): {llm.last_latency_ms:.2f} ms")
    print("NOTE: real latency = this plumbing + your model's compute time.")
    print("      Set GUEST_FAST_BASE_URL + GUEST_FAST_MODEL and pass --live N for real p50/p95.")
    await llm.aclose()


async def main() -> None:
    live = 0
    if "--live" in sys.argv:
        i = sys.argv.index("--live")
        live = int(sys.argv[i + 1]) if i + 1 < len(sys.argv) else 30
    show_prompt()
    bench_deterministic()
    await bench_llm(live)


if __name__ == "__main__":
    asyncio.run(main())
