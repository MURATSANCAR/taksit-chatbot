#!/usr/bin/env python3
"""E2E multi-turn against live /v1/chat (nanobase or local).

Usage:
  BASE_URL=http://127.0.0.1:8011 python scripts/run_e2e_api.py
  BASE_URL=http://localhost:8000 python scripts/run_e2e_api.py --limit 20

Asserts:
  - HTTP 200
  - session_id stable across turns
  - revision non-decreasing
  - expect_intent / phase / cta / cards when specified
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCENARIOS = ROOT / "tests" / "golden" / "guest_multiturn" / "scenarios.jsonl"
REPORT = ROOT / "tests" / "golden" / "guest_multiturn" / "e2e_report.json"


def post_chat(base: str, payload: dict, timeout: float = 30.0) -> dict:
    url = base.rstrip("/") + "/v1/chat"
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read().decode("utf-8")
        return json.loads(body)


def extract_intent(resp: dict) -> str | None:
    diag = resp.get("diagnostics") or {}
    return diag.get("intent") or resp.get("decision")


def has_cards(resp: dict) -> bool:
    cards = resp.get("campaigns") or resp.get("cards") or []
    if cards:
        return True
    for m in resp.get("messages") or []:
        if m.get("type") == "campaign_card":
            return True
    return False


def has_cta(resp: dict) -> bool:
    cta = resp.get("cta") or resp.get("membership_cta")
    if cta:
        return True
    # sometimes nested
    return False


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base-url", default=os.environ.get("BASE_URL", "http://127.0.0.1:8011"))
    ap.add_argument("--limit", type=int, default=0, help="Max scenarios (0=all)")
    ap.add_argument("--timeout", type=float, default=30.0)
    args = ap.parse_args()

    if not SCENARIOS.exists():
        print("Missing scenarios; run generate_multiturn_suite.py")
        return 2

    scenarios = [json.loads(l) for l in SCENARIOS.read_text(encoding="utf-8").splitlines() if l.strip()]
    if args.limit:
        scenarios = scenarios[: args.limit]

    results = []
    ok_sc = 0
    total_turns = 0
    ok_turns = 0

    for sc in scenarios:
        session_id = "new"
        revision = 0
        sc_errors: list[str] = []
        turn_logs = []

        for i, turn in enumerate(sc["turns"]):
            total_turns += 1
            payload = {
                "session_id": session_id,
                "message": turn["utterance"],
            }
            # Many deployments accept optional revision
            if revision:
                payload["revision"] = revision
            # guest: no user_id
            try:
                resp = post_chat(args.base_url, payload, timeout=args.timeout)
            except urllib.error.HTTPError as e:
                body = e.read().decode("utf-8", errors="replace")
                sc_errors.append(f"turn{i} HTTP {e.code}: {body[:200]}")
                turn_logs.append({"turn": i, "error": str(e.code)})
                continue
            except Exception as e:
                sc_errors.append(f"turn{i} EXC: {e}")
                turn_logs.append({"turn": i, "error": str(e)})
                continue

            # session continuity
            new_sid = resp.get("session_id") or session_id
            if session_id not in ("new", "", None) and new_sid != session_id:
                sc_errors.append(f"turn{i} session_id changed {session_id} → {new_sid}")
            session_id = new_sid

            new_rev = resp.get("revision")
            if new_rev is not None:
                if revision and int(new_rev) < int(revision):
                    sc_errors.append(f"turn{i} revision decreased {revision} → {new_rev}")
                revision = int(new_rev)

            intent = extract_intent(resp)
            phase = resp.get("phase")
            turn_ok = True
            terrors = []

            if "expect_intent_in" in turn and intent:
                # decision may be GUEST_* — normalize
                norm = str(intent).replace("GUEST_", "")
                allowed = set(turn["expect_intent_in"]) | {f"GUEST_{x}" for x in turn["expect_intent_in"]}
                # also allow decision values like GUEST_RECOMMENDATION for needs
                if norm not in turn["expect_intent_in"] and intent not in allowed:
                    if not (
                        "NEEDS_ANALYSIS" in turn["expect_intent_in"]
                        and str(intent) in (
                            "GUEST_RECOMMENDATION",
                            "NEEDS_ANALYSIS",
                            "COMPLEX_NEED",
                            "GUEST_CLARIFY",
                        )
                    ):
                        if not (
                            "REFINEMENT" in turn["expect_intent_in"]
                            and "REFIN" in str(intent).upper()
                        ):
                            if not (
                                "FAQ" in turn["expect_intent_in"]
                                and ("FAQ" in str(intent).upper() or phase == "FAQ")
                            ):
                                if not (
                                    "OOS" in turn["expect_intent_in"]
                                    and (
                                        "OOS" in str(intent).upper()
                                        or "SAFE" in str(intent).upper()
                                        or phase == "SAFE_FAILURE"
                                    )
                                ):
                                    terrors.append(f"intent={intent}")

            if turn.get("expect_has_cards") is True and not has_cards(resp):
                # soft if CLARIFY
                if phase not in ("CLARIFY",):
                    terrors.append("missing_cards")
            if turn.get("expect_cta") is True and not has_cta(resp):
                terrors.append("missing_cta")

            if terrors:
                turn_ok = False
                sc_errors.append(f"turn{i} {turn['utterance'][:40]}: {terrors} phase={phase} intent={intent}")
            else:
                ok_turns += 1

            turn_logs.append(
                {
                    "turn": i,
                    "utterance": turn["utterance"],
                    "phase": phase,
                    "intent": intent,
                    "revision": revision,
                    "cards": has_cards(resp),
                    "cta": has_cta(resp),
                    "ok": turn_ok,
                }
            )

        if not sc_errors:
            ok_sc += 1
        results.append(
            {
                "id": sc["id"],
                "name": sc["name"],
                "ok": not sc_errors,
                "errors": sc_errors[:5],
                "turns": turn_logs,
            }
        )

    report = {
        "base_url": args.base_url,
        "scenarios": len(scenarios),
        "scenarios_ok": ok_sc,
        "scenario_accuracy": round(ok_sc / len(scenarios), 4) if scenarios else 0,
        "turns": total_turns,
        "turns_ok": ok_turns,
        "turn_accuracy": round(ok_turns / total_turns, 4) if total_turns else 0,
        "results": results,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"E2E {args.base_url}")
    print(f"Scenarios: {ok_sc}/{len(scenarios)} ({report['scenario_accuracy']:.1%})")
    print(f"Turns: {ok_turns}/{total_turns} ({report['turn_accuracy']:.1%})")
    print(f"Report → {REPORT}")
    failed = [r for r in results if not r["ok"]]
    for r in failed[:8]:
        print(f"  FAIL {r['id']} {r['name']}: {r['errors'][:2]}")

    if report["scenario_accuracy"] < 0.75:
        print("BELOW THRESHOLD")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
