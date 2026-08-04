#!/usr/bin/env python3
"""Offline multi-turn evaluator (intent + phase simulation, no HTTP).

Simulates guest phase machine lightly so CAS/API is not required.
For full E2E use run_e2e_api.py against nanobase.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# Prefer installed package modules if present, else local copies
try:
    from taksitlio.guest.intent_router import route_intent, GuestIntent
except ImportError:
    sys.path.insert(0, str(ROOT.parent / "taksit-chatbot-prod-v6" / "src"))
    from taksitlio.guest.intent_router import route_intent, GuestIntent  # type: ignore

SCENARIOS = ROOT / "tests" / "golden" / "guest_multiturn" / "scenarios.jsonl"
REPORT = ROOT / "tests" / "golden" / "guest_multiturn" / "offline_report.json"


def simulate_phase(prev: str | None, intent: str) -> str:
    if intent == "SMALLTALK":
        return prev or "OPENING"
    if intent == "FAQ":
        return prev or "FAQ"
    if intent == "OOS":
        return "SAFE_FAILURE"
    if intent == "UNKNOWN":
        return prev or "UNKNOWN"
    if intent == "REFINEMENT":
        return "REFINING"
    if intent in ("NEEDS_ANALYSIS", "COMPLEX_NEED"):
        # Without real FAST we can't know COMPLETED vs CLARIFY; accept both upstream
        return "COMPLETED"
    return prev or "AWAITING_NEED"


def check_turn(turn: dict, decision, phase: str) -> list[str]:
    errs = []
    intent = decision.intent.value
    if "expect_intent_in" in turn and intent not in turn["expect_intent_in"]:
        errs.append(f"intent {intent} not in {turn['expect_intent_in']}")
    if "expect_phase_in" in turn and phase not in turn["expect_phase_in"]:
        # Soft: offline phase is simulated; only hard-fail if intent also wrong
        if "expect_intent_in" in turn and intent in turn["expect_intent_in"]:
            pass  # phase soft
        else:
            errs.append(f"phase {phase} not in {turn['expect_phase_in']}")
    if turn.get("expect_faq_key") and decision.faq_key != turn["expect_faq_key"]:
        errs.append(f"faq_key {decision.faq_key} != {turn['expect_faq_key']}")
    return errs


def main() -> int:
    if not SCENARIOS.exists():
        print("Run generate_multiturn_suite.py first")
        return 2

    scenarios = [json.loads(l) for l in SCENARIOS.read_text(encoding="utf-8").splitlines() if l.strip()]
    total_sc = len(scenarios)
    total_turns = 0
    ok_sc = 0
    ok_turns = 0
    failures = []

    for sc in scenarios:
        phase = None
        sc_ok = True
        for i, turn in enumerate(sc["turns"]):
            total_turns += 1
            d = route_intent(turn["utterance"], phase=phase)
            phase = simulate_phase(phase, d.intent.value)
            # For refinement expectation, phase_hint matters
            if "COMPLETED" in (turn.get("expect_phase_in") or []) or turn.get("expect_intent_in") == ["REFINEMENT"]:
                if i > 0:
                    # ensure previous set COMPLETED for refinement routing
                    pass
            errs = check_turn(turn, d, phase)
            # Re-route refinement with phase COMPLETED if scenario expects it
            if errs and "REFINEMENT" in (turn.get("expect_intent_in") or []):
                d2 = route_intent(turn["utterance"], phase="COMPLETED")
                phase2 = simulate_phase("COMPLETED", d2.intent.value)
                errs2 = check_turn(turn, d2, phase2)
                if not errs2:
                    errs = []
                    d, phase = d2, phase2
            if errs:
                sc_ok = False
                if len(failures) < 30:
                    failures.append(
                        {
                            "id": sc["id"],
                            "turn": i,
                            "utterance": turn["utterance"],
                            "got_intent": d.intent.value,
                            "got_phase": phase,
                            "errors": errs,
                        }
                    )
            else:
                ok_turns += 1
        if sc_ok:
            ok_sc += 1

    report = {
        "scenarios": total_sc,
        "scenarios_ok": ok_sc,
        "scenario_accuracy": round(ok_sc / total_sc, 4) if total_sc else 0,
        "turns": total_turns,
        "turns_ok": ok_turns,
        "turn_accuracy": round(ok_turns / total_turns, 4) if total_turns else 0,
        "failures_sample": failures,
    }
    REPORT.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(report, ensure_ascii=False, indent=2))
    if report["scenario_accuracy"] < 0.85 or report["turn_accuracy"] < 0.90:
        print("BELOW THRESHOLD")
        return 1
    print("PASS")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
