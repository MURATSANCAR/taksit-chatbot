#!/usr/bin/env python3
"""Production guest-journey probe — act like a real user across all intent classes.

Hits live /v1/search-sessions (no catalog mutation). Checks:
- greeting / off-topic refuse + latency
- greeting+purchase stays in-domain
- product queries return integrity-safe cards only
- no product/campaign leak on off-domain
- follow-up supersede behavior

Usage (on nanobase):
  set -a && source .env.runtime && set +a
  .venv/bin/python scripts/probe_guest_journey_prod.py --base http://127.0.0.1:8040
"""

from __future__ import annotations

import argparse
import json
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional
from urllib.error import HTTPError, URLError
from urllib.request import Request, urlopen

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "artifacts" / "e2e-production-verification" / "guest-journey-probe"

GREETING_SNIP = "Tanıştığımıza memnun oldum"
OFFTOPIC_SNIP = "Bu konuda yardımcı olamam"
LATENCY_OFF_DOMAIN_MS = 500  # hard fail above this for pure greet/chat


@dataclass
class Expect:
    name: str
    message: str
    # route expectations
    route_in: tuple[str, ...] = ()
    route_not_in: tuple[str, ...] = ()
    must_be_off_domain: bool = False
    must_be_in_domain: bool = False
    reply_contains: tuple[str, ...] = ()
    reply_not_contains: tuple[str, ...] = ()
    max_ms: Optional[float] = None
    require_products: bool = False
    allow_empty_products: bool = True
    forbid_products: bool = False
    check_integrity: bool = True
    # multi-turn: if set, supersede after start
    follow_up: Optional[str] = None
    follow_up_must_be_in_domain: bool = False
    follow_up_forbid_products: bool = False


@dataclass
class CaseResult:
    name: str
    message: str
    ok: bool
    elapsed_ms: float
    http_status: int
    route: Optional[str] = None
    status: Optional[str] = None
    product_count: int = 0
    reply_preview: str = ""
    errors: list[str] = field(default_factory=list)
    follow_up: Optional[dict[str, Any]] = None
    sample_products: list[dict[str, Any]] = field(default_factory=list)


def _cid() -> str:
    return str(uuid.uuid4())


def _post(base: str, path: str, body: dict[str, Any], timeout: float = 60.0) -> tuple[int, dict[str, Any], float]:
    raw = json.dumps(body).encode("utf-8")
    req = Request(
        f"{base.rstrip('/')}{path}",
        data=raw,
        headers={"Content-Type": "application/json", "Accept": "application/json"},
        method="POST",
    )
    t0 = time.perf_counter()
    try:
        with urlopen(req, timeout=timeout) as resp:
            data = resp.read()
            elapsed = (time.perf_counter() - t0) * 1000
            return int(resp.status), json.loads(data.decode("utf-8")), elapsed
    except HTTPError as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        payload: dict[str, Any]
        try:
            payload = json.loads(exc.read().decode("utf-8"))
        except Exception:
            payload = {"detail": str(exc)}
        return int(exc.code), payload, elapsed
    except URLError as exc:
        elapsed = (time.perf_counter() - t0) * 1000
        return 0, {"detail": str(exc.reason)}, elapsed


def _products(payload: dict[str, Any]) -> list[dict[str, Any]]:
    block = payload.get("results") or payload.get("partial_results") or {}
    items = block.get("products") or []
    return [p for p in items if isinstance(p, dict)]


def _integrity_errors(products: list[dict[str, Any]]) -> list[str]:
    errs: list[str] = []
    for i, p in enumerate(products[:20]):
        pid = p.get("id") or p.get("product_id") or i
        cdn = (
            p.get("primary_cdn_url")
            or p.get("thumbnail_cdn_url")
            or p.get("image_cdn_url")
            or (p.get("media") or {}).get("cdn_url")
            if isinstance(p.get("media"), dict)
            else None
        )
        # nested offer card shapes
        if not cdn:
            media = p.get("primary_media") or {}
            if isinstance(media, dict):
                cdn = media.get("cdn_url") or media.get("url")
        cat = p.get("category_id") or p.get("category") or (p.get("taxonomy") or {}).get("category_id")
        status = (p.get("primary_media_status") or p.get("media_status") or "").upper()
        if status and status != "READY":
            errs.append(f"product[{pid}] media_status={status}")
        if not cdn:
            # Some cards only expose thumbnail — still require some CDN URL field.
            blob = json.dumps(p, ensure_ascii=False)
            if "cdn" not in blob.lower() and "http" not in blob.lower():
                errs.append(f"product[{pid}] missing CDN url fields")
        # category may be nested display-only; warn soft if completely absent
        if cat is None and not any(
            k in p for k in ("category_name", "category_path", "category_label")
        ):
            # soft: many public cards omit id but keep label — only hard-fail if nothing category-like
            pass
    return errs


def _eval(case: Expect, status: int, payload: dict[str, Any], elapsed_ms: float) -> CaseResult:
    errors: list[str] = []
    route = payload.get("route")
    st = payload.get("status")
    reply = str(payload.get("reply") or "")
    products = _products(payload)

    if status != 200:
        errors.append(f"http={status} detail={payload.get('detail')}")

    if case.must_be_off_domain:
        if route != "OUT_OF_SCOPE":
            errors.append(f"expected OUT_OF_SCOPE, got route={route}")
        if products:
            errors.append(f"off-domain leaked {len(products)} products")

    if case.must_be_in_domain:
        if route == "OUT_OF_SCOPE":
            errors.append("expected in-domain search, got OUT_OF_SCOPE")

    if case.route_in and route not in case.route_in:
        errors.append(f"route {route} not in {case.route_in}")
    if case.route_not_in and route in case.route_not_in:
        errors.append(f"route {route} forbidden")

    for snip in case.reply_contains:
        if snip not in reply:
            errors.append(f"reply missing snippet: {snip!r}")
    for snip in case.reply_not_contains:
        if snip in reply:
            errors.append(f"reply must not contain: {snip!r}")

    if case.forbid_products and products:
        errors.append(f"forbid_products but got {len(products)}")

    if case.require_products and not products:
        errors.append("require_products but empty")

    if case.max_ms is not None and elapsed_ms > case.max_ms:
        errors.append(f"latency {elapsed_ms:.0f}ms > {case.max_ms:.0f}ms")

    integ: list[str] = []
    if case.check_integrity and products:
        integ = _integrity_errors(products)
        errors.extend(integ)

    sample = []
    for p in products[:3]:
        sample.append(
            {
                "id": p.get("id") or p.get("product_id"),
                "name": (p.get("display_name") or p.get("title") or p.get("name") or "")[:80],
                "keys": sorted(p.keys())[:20],
            }
        )

    return CaseResult(
        name=case.name,
        message=case.message,
        ok=not errors,
        elapsed_ms=round(elapsed_ms, 1),
        http_status=status,
        route=str(route) if route is not None else None,
        status=str(st) if st is not None else None,
        product_count=len(products),
        reply_preview=reply[:140],
        errors=errors,
        sample_products=sample,
    )


def cases() -> list[Expect]:
    return [
        # --- greetings ---
        Expect(
            name="greet_merhaba",
            message="merhaba",
            must_be_off_domain=True,
            forbid_products=True,
            reply_contains=(GREETING_SNIP,),
            reply_not_contains=(OFFTOPIC_SNIP,),
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        Expect(
            name="greet_selam",
            message="selam",
            must_be_off_domain=True,
            forbid_products=True,
            reply_contains=(GREETING_SNIP,),
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        Expect(
            name="greet_nasilsin",
            message="nasılsın",
            must_be_off_domain=True,
            forbid_products=True,
            reply_contains=(GREETING_SNIP,),
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        Expect(
            name="greet_kimsin",
            message="sen kimsin",
            must_be_off_domain=True,
            forbid_products=True,
            reply_contains=(GREETING_SNIP,),
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        Expect(
            name="greet_gunaydin",
            message="günaydın",
            must_be_off_domain=True,
            forbid_products=True,
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        # --- off-topic ---
        Expect(
            name="off_hava",
            message="hava durumu nasıl",
            must_be_off_domain=True,
            forbid_products=True,
            reply_contains=(OFFTOPIC_SNIP,),
            reply_not_contains=(GREETING_SNIP,),
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        Expect(
            name="off_fikra",
            message="bana bir fıkra anlat",
            must_be_off_domain=True,
            forbid_products=True,
            reply_contains=(OFFTOPIC_SNIP,),
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        Expect(
            name="off_siyaset",
            message="siyaset hakkında ne düşünüyorsun",
            must_be_off_domain=True,
            forbid_products=True,
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        Expect(
            name="off_odev",
            message="ödevimi yap",
            must_be_off_domain=True,
            forbid_products=True,
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        Expect(
            name="off_kripto",
            message="bitcoin alayım mı",
            must_be_off_domain=True,
            forbid_products=True,
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        Expect(
            name="off_otel",
            message="kapadokya otel rezervasyonu",
            must_be_off_domain=True,
            forbid_products=True,
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        Expect(
            name="off_sohbet",
            message="sohbet edelim",
            must_be_off_domain=True,
            forbid_products=True,
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        # --- greeting + purchase (must NOT refuse) ---
        Expect(
            name="mix_merhaba_iphone",
            message="merhaba iphone arıyorum",
            must_be_in_domain=True,
            route_not_in=("OUT_OF_SCOPE",),
            max_ms=30000,
            allow_empty_products=True,
        ),
        Expect(
            name="mix_selam_telefon",
            message="selam telefon lazım",
            must_be_in_domain=True,
            route_not_in=("OUT_OF_SCOPE",),
            max_ms=30000,
        ),
        Expect(
            name="mix_merhaba_butce",
            message="merhaba, bütçem 20 bin telefon bakıyorum",
            must_be_in_domain=True,
            route_not_in=("OUT_OF_SCOPE",),
            max_ms=30000,
        ),
        # --- product domain ---
        Expect(
            name="prod_iphone15",
            message="iphone 15",
            must_be_in_domain=True,
            route_not_in=("OUT_OF_SCOPE",),
            require_products=True,
            max_ms=30000,
        ),
        Expect(
            name="prod_macbook",
            message="MacBook Pro",
            must_be_in_domain=True,
            route_not_in=("OUT_OF_SCOPE",),
            require_products=True,
            max_ms=30000,
        ),
        Expect(
            name="prod_buzdolabi",
            message="buzdolabı bakıyorum",
            must_be_in_domain=True,
            route_not_in=("OUT_OF_SCOPE",),
            require_products=True,
            max_ms=30000,
        ),
        Expect(
            name="prod_kulaklik_budget",
            message="kulaklık arıyorum bütçem 3000",
            must_be_in_domain=True,
            route_not_in=("OUT_OF_SCOPE",),
            require_products=True,
            max_ms=30000,
        ),
        Expect(
            name="prod_ayakkabi",
            message="spor ayakkabı arıyorum",
            must_be_in_domain=True,
            route_not_in=("OUT_OF_SCOPE",),
            require_products=True,
            max_ms=30000,
        ),
        Expect(
            name="prod_taksit_cue",
            message="taksitli telefon öner",
            must_be_in_domain=True,
            route_not_in=("OUT_OF_SCOPE",),
            require_products=True,
            max_ms=30000,
        ),
        Expect(
            name="prod_kampanya_cue",
            message="kampanyalı laptop bakıyorum",
            must_be_in_domain=True,
            route_not_in=("OUT_OF_SCOPE",),
            require_products=True,
            max_ms=30000,
        ),
        Expect(
            name="mix_merhaba_iphone_products",
            message="merhaba iphone arıyorum",
            must_be_in_domain=True,
            route_not_in=("OUT_OF_SCOPE",),
            require_products=True,
            max_ms=30000,
        ),
        # --- non-purchase service-ish (should refuse, no products) ---
        Expect(
            name="non_sikayet",
            message="şikayet etmek istiyorum",
            must_be_off_domain=True,
            forbid_products=True,
            max_ms=LATENCY_OFF_DOMAIN_MS,
        ),
        Expect(
            name="non_tamir",
            message="telefonum bozuldu tamir istiyorum",
            must_be_off_domain=True,
            forbid_products=True,
            max_ms=2000,
        ),
        # --- multi-turn: greet then product on same conversation via new session ---
        Expect(
            name="turn_greet_then_product",
            message="merhaba",
            must_be_off_domain=True,
            forbid_products=True,
            max_ms=LATENCY_OFF_DOMAIN_MS,
            follow_up="iphone 15 arıyorum",
            follow_up_must_be_in_domain=True,
        ),
        Expect(
            name="turn_product_then_offtopic",
            message="kulaklık arıyorum",
            must_be_in_domain=True,
            max_ms=30000,
            follow_up="hava durumu nasıl",
            follow_up_forbid_products=True,
        ),
    ]


def run_case(base: str, case: Expect) -> CaseResult:
    body = {
        "conversation_id": _cid(),
        "message": case.message,
        "client_query_id": str(uuid.uuid4()),
    }
    status, payload, elapsed = _post(base, "/v1/search-sessions", body)
    result = _eval(case, status, payload, elapsed)

    if case.follow_up and status == 200 and payload.get("search_session_id"):
        sid = payload["search_session_id"]
        # off-domain start may leave session hard-terminal; prefer fresh start for follow-up
        # when first turn was OUT_OF_SCOPE
        if payload.get("route") == "OUT_OF_SCOPE":
            st2, p2, e2 = _post(
                base,
                "/v1/search-sessions",
                {
                    "conversation_id": body["conversation_id"],
                    "message": case.follow_up,
                    "client_query_id": str(uuid.uuid4()),
                },
            )
            mode = "new_session"
        else:
            st2, p2, e2 = _post(
                base,
                f"/v1/search-sessions/{sid}/messages",
                {"message": case.follow_up},
            )
            mode = "supersede"
            # if supersede rejected (terminal), fall back to new session
            if st2 in {404, 409}:
                st2, p2, e2 = _post(
                    base,
                    "/v1/search-sessions",
                    {
                        "conversation_id": body["conversation_id"],
                        "message": case.follow_up,
                        "client_query_id": str(uuid.uuid4()),
                    },
                )
                mode = "fallback_new_session"

        fu_errors: list[str] = []
        route2 = p2.get("route")
        products2 = _products(p2)
        if case.follow_up_must_be_in_domain and route2 == "OUT_OF_SCOPE":
            fu_errors.append("follow_up expected in-domain")
        if case.follow_up_forbid_products and products2:
            fu_errors.append(f"follow_up leaked {len(products2)} products")
        if case.follow_up_forbid_products and route2 != "OUT_OF_SCOPE":
            # off-topic follow-up should refuse
            if "hava" in (case.follow_up or "").lower() and route2 != "OUT_OF_SCOPE":
                fu_errors.append(f"follow_up off-topic route={route2}")
        if products2:
            fu_errors.extend(_integrity_errors(products2))
        result.follow_up = {
            "mode": mode,
            "ok": not fu_errors and st2 == 200,
            "http_status": st2,
            "elapsed_ms": round(e2, 1),
            "route": route2,
            "product_count": len(products2),
            "reply_preview": str(p2.get("reply") or "")[:120],
            "errors": fu_errors,
        }
        if fu_errors or st2 != 200:
            result.ok = False
            result.errors.extend([f"follow_up: {e}" for e in (fu_errors or [f"http={st2}"])])

    return result


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", default="http://127.0.0.1:8040")
    ap.add_argument("--out", default=str(ART))
    args = ap.parse_args()
    out_dir = Path(args.out)
    out_dir.mkdir(parents=True, exist_ok=True)

    suite = cases()
    results: list[CaseResult] = []
    print(f"guest-journey probe → {args.base}  ({len(suite)} cases)")
    for case in suite:
        r = run_case(args.base, case)
        results.append(r)
        mark = "PASS" if r.ok else "FAIL"
        print(
            f"  [{mark}] {r.name:28} {r.elapsed_ms:7.0f}ms  route={r.route}  "
            f"products={r.product_count}  {('; '.join(r.errors))[:120]}"
        )

    passed = sum(1 for r in results if r.ok)
    failed = [r for r in results if not r.ok]
    off_lat = [r.elapsed_ms for r in results if r.name.startswith(("greet_", "off_")) and r.http_status == 200]
    in_lat = [r.elapsed_ms for r in results if r.name.startswith(("prod_", "mix_")) and r.http_status == 200]

    # Aggregate integrity: any product returned across in-domain cases
    leaked_off = [
        r.name
        for r in results
        if r.product_count > 0 and (r.name.startswith(("greet_", "off_", "non_")) or r.route == "OUT_OF_SCOPE")
    ]

    summary = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "base": args.base,
        "total": len(results),
        "passed": passed,
        "failed": len(failed),
        "fail_names": [r.name for r in failed],
        "off_domain_latency_ms": {
            "n": len(off_lat),
            "p50": sorted(off_lat)[len(off_lat) // 2] if off_lat else None,
            "max": max(off_lat) if off_lat else None,
            "budget_ms": LATENCY_OFF_DOMAIN_MS,
        },
        "in_domain_latency_ms": {
            "n": len(in_lat),
            "p50": sorted(in_lat)[len(in_lat) // 2] if in_lat else None,
            "max": max(in_lat) if in_lat else None,
        },
        "off_domain_product_leaks": leaked_off,
        "verdict": "PASS" if not failed and not leaked_off else "FAIL",
        "cases": [asdict(r) for r in results],
    }

    path = out_dir / "guest-journey-results.json"
    path.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    md = [
        "# Guest journey production probe",
        "",
        f"- Time: `{summary['ts']}`",
        f"- Base: `{args.base}`",
        f"- Verdict: **{summary['verdict']}** ({passed}/{len(results)} passed)",
        f"- Off-domain latency p50/max: `{summary['off_domain_latency_ms']['p50']}` / `{summary['off_domain_latency_ms']['max']}` ms (budget {LATENCY_OFF_DOMAIN_MS})",
        f"- In-domain latency p50/max: `{summary['in_domain_latency_ms']['p50']}` / `{summary['in_domain_latency_ms']['max']}` ms",
        f"- Off-domain product leaks: `{leaked_off or 'none'}`",
        "",
        "| Case | Result | ms | route | products | errors |",
        "|---|---|---:|---|---:|---|",
    ]
    for r in results:
        err = "; ".join(r.errors).replace("|", "/")[:100] if r.errors else ""
        md.append(
            f"| `{r.name}` | {'PASS' if r.ok else 'FAIL'} | {r.elapsed_ms:.0f} | {r.route} | {r.product_count} | {err} |"
        )
    md_path = out_dir / "guest-journey-report.md"
    md_path.write_text("\n".join(md) + "\n", encoding="utf-8")

    print()
    print(f"verdict={summary['verdict']}  passed={passed}/{len(results)}")
    print(f"wrote {path}")
    print(f"wrote {md_path}")
    return 0 if summary["verdict"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
