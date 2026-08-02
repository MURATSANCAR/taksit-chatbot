#!/usr/bin/env python3
"""P17.1 — V3 NeedProfile residual export (P17-V3-RESIDUAL-001).

Runs the locked LoRA-v3 sidecar against the development set, stores raw +
parsed + evaluated responses, auto-codes primary/secondary causes (no QUANT),
and writes the four required artifacts under artifacts/p17/v3/.

Campaign Gate is never touched. Does not claim QUALITY_READY.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import json
import os
import re
import time
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping, Optional, Sequence

import httpx

from taksitlio.evaluation.dataset import load_jsonl
from taksitlio.evaluation.runtime.fast_ab import PROMPT_VERSION, SCHEMA_VERSION
from taksitlio.evaluation.runtime.fast_quality import (
    _concept_texts,
    _correction_texts,
)
from taksitlio.understanding.fast.remote import (
    _DEFAULT_SYSTEM_PROMPT,
    _need_profile_schema,
)
from taksitlio.understanding.fast.hybrid import hybrid_final_constraints
from taksitlio.understanding.fast.deterministic import DeterministicFastExtractor


ROOT = Path(__file__).resolve().parents[1]
EXPERIMENT_ID = "P17-V3-RESIDUAL-001"
DEFAULT_DEV = ROOT / "evaluation" / "datasets" / "development" / "tr-category-dev.v4.jsonl"
DEFAULT_OUT = ROOT / "artifacts" / "p17" / "v3"

PATTERN_IDS = (
    "NEG_SIMPLE",
    "CORRECTION_X_NOT_Y",
    "CORRECTION_RETRACTION",
    "NEGATION_OF_NEGATION",
    "MULTI_POS_SINGLE_NEG",
    "BUDGET_PLUS_CORRECTION",
    "SLANG",
)


def _utc_now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _concepts_list(bag: Mapping[str, Any] | None, key: str) -> list[str]:
    items = (bag or {}).get(key) or []
    out: list[str] = []
    for item in items:
        if not isinstance(item, Mapping):
            continue
        for k in ("concept", "surface_form", "normalized", "text", "value"):
            v = item.get(k)
            if isinstance(v, str) and v.strip():
                out.append(v.strip())
                break
    return out


def _gold_view(sc: Mapping[str, Any] | None) -> dict[str, Any]:
    return {
        "positive": _concepts_list(sc, "positive"),
        "negative": _concepts_list(sc, "negative"),
        "correction": bool((sc or {}).get("corrections")),
        "corrections": list((sc or {}).get("corrections") or []),
        "budget": None,
    }


def _pred_view_from_profile(profile: Mapping[str, Any] | None) -> dict[str, Any]:
    sc = (profile or {}).get("semantic_constraints") or {}
    if not isinstance(sc, Mapping):
        sc = {}
    budget = (profile or {}).get("budget")
    return {
        "positive": _concepts_list(sc, "positive"),
        "negative": _concepts_list(sc, "negative"),
        "correction": bool(sc.get("corrections")),
        "corrections": list(sc.get("corrections") or []),
        "budget": budget if isinstance(budget, Mapping) else None,
    }


def _pred_view_from_bag(bag: Mapping[str, Any] | None) -> dict[str, Any]:
    bag = bag or {}
    return {
        "positive": _concepts_list(bag, "positive"),
        "negative": _concepts_list(bag, "negative"),
        "correction": bool(bag.get("corrections")),
        "corrections": list(bag.get("corrections") or []),
        "budget": None,
    }


def linguistic_patterns(utterance: str) -> list[str]:
    ul = utterance.lower()
    tags: list[str] = []
    if re.search(r"istemiyorum\s+demedim|demedim.*istem", ul):
        tags.append("NEGATION_OF_NEGATION")
    if re.search(r"vazgeç|boşver|yerine\s", ul):
        tags.append("CORRECTION_RETRACTION")
    if "değil" in ul and "NEGATION_OF_NEGATION" not in tags:
        tags.append("CORRECTION_X_NOT_Y")
    if re.search(r"istemiyorum|istemem|olmasın", ul) and "değil" not in ul:
        tags.append("NEG_SIMPLE")
    if re.search(r"\bveya\b|\bya da\b", ul) and re.search(
        r"olmasın|istemiyorum|değil", ul
    ):
        tags.append("MULTI_POS_SINGLE_NEG")
    if re.search(r"\d+\s*bin|\d+\s*tl|bütçe|civarı", ul) and re.search(
        r"değil|istemiyorum|vazgeç|boşver", ul
    ):
        tags.append("BUDGET_PLUS_CORRECTION")
    if re.search(r"sarmıyor|fln|falan|baya|vs\.?", ul):
        tags.append("SLANG")
    return tags


def _sets_from_view(view: Mapping[str, Any]) -> tuple[set[str], set[str], set[str]]:
    pos = _concept_texts([{"concept": c} for c in (view.get("positive") or [])])
    neg = _concept_texts([{"concept": c} for c in (view.get("negative") or [])])
    corr = _correction_texts(view.get("corrections") or [])
    if view.get("correction") and not corr:
        # boolean-only gold: treat any predicted correction pair as needed presence
        corr = {"__correction_present__"} if view.get("correction") else set()
    return pos, neg, corr


def _pred_corr_set(view: Mapping[str, Any]) -> set[str]:
    corr = _correction_texts(view.get("corrections") or [])
    if view.get("correction") and not corr:
        return {"__correction_present__"}
    return corr


def assign_causes(
    *,
    status: str,
    gold: Mapping[str, Any],
    pred: Mapping[str, Any],
    schema_valid: bool,
    forbidden_field: bool,
    hallucinated: bool,
    truncated: bool,
    timeout: bool,
    non_json: bool,
) -> tuple[Optional[str], list[str], dict[str, bool]]:
    """Primary/secondary cause coding. QUANT is never assigned in P17.1."""

    g_pos, g_neg, g_corr = _sets_from_view(gold)
    p_pos, p_neg, _ = _sets_from_view(pred)
    p_corr = _pred_corr_set(pred)

    # For boolean-only gold corrections, presence of any correction is enough.
    if g_corr == {"__correction_present__"}:
        corr_ok = bool(p_corr)
        g_corr_score = {"__correction_present__"} if gold.get("correction") else set()
        p_corr_score = {"__correction_present__"} if pred.get("correction") else set()
    else:
        corr_ok = g_corr <= p_corr if g_corr else True
        g_corr_score = g_corr
        p_corr_score = p_corr

    conflict = bool(p_pos & p_neg)
    gold_nonempty = bool(g_pos or g_neg or g_corr_score)
    pred_empty = not (p_pos or p_neg or p_corr_score)
    pos_miss = bool(g_pos - p_pos)
    neg_miss = bool(g_neg - p_neg)
    corr_miss = bool(g_corr_score - p_corr_score) if g_corr_score else False
    over = bool((p_pos - g_pos) or (p_neg - g_neg))
    # Matcher: NeedProfile exact match vs gold → not a model residual failure.
    model_ok = (
        schema_valid
        and not forbidden_field
        and not hallucinated
        and not conflict
        and not pos_miss
        and not neg_miss
        and not corr_miss
        and not over
        and status == "ok"
    )

    flags = {
        "conflict": conflict,
        "forbidden": forbidden_field or hallucinated,
        "pos_miss": pos_miss,
        "neg_miss": neg_miss,
        "corr_miss": corr_miss,
        "over_extract": over,
        "empty": gold_nonempty and pred_empty,
        "model_ok": model_ok,
    }

    ordered: list[str] = []
    if timeout:
        ordered.append("RUNTIME_TIMEOUT")
    if truncated:
        ordered.append("RUNTIME_TRUNCATED")
    if non_json:
        ordered.append("RUNTIME_NON_JSON")
    if not schema_valid and not non_json and not timeout:
        ordered.append("RUNTIME_SCHEMA_FAIL")
    if forbidden_field:
        ordered.append("MODEL_FORBIDDEN_FIELD")
    if hallucinated:
        ordered.append("MODEL_HALLUCINATED_ENTITY")
    if conflict:
        ordered.append("MODEL_CONFLICT")
    if gold_nonempty and pred_empty:
        ordered.append("MODEL_EMPTY")
    if corr_miss:
        ordered.append("MODEL_CORR_MISS")
    if pos_miss:
        ordered.append("MODEL_POS_MISS")
    if neg_miss:
        ordered.append("MODEL_NEG_MISS")
    if over and not (gold_nonempty and pred_empty):
        ordered.append("MODEL_OVER_EXTRACT")

    # Deduplicate preserving order
    seen: set[str] = set()
    causes: list[str] = []
    for c in ordered:
        if c not in seen:
            seen.add(c)
            causes.append(c)

    if not causes:
        return None, [], flags
    return causes[0], causes[1:], flags


def _scan_forbidden_leaves(node: Any) -> list[str]:
    hits: list[str] = []
    if isinstance(node, str):
        lowered = node.strip().lower()
        if any(p in lowered for p in ("fixture.", "category-", "cat_")):
            hits.append(node)
        elif len(lowered) >= 32 and lowered.count("-") >= 4:
            parts = lowered.replace("-", "")
            if parts and all(c in "0123456789abcdef" for c in parts):
                hits.append(node)
    elif isinstance(node, Mapping):
        for v in node.values():
            hits.extend(_scan_forbidden_leaves(v))
    elif isinstance(node, list):
        for v in node:
            hits.extend(_scan_forbidden_leaves(v))
    return hits


_BANKISH = re.compile(
    r"\b(fibabanka|garanti|akbank|yap[iı]kredi|i[sş]bank|ziraat|vak[iı]fbank|"
    r"qnb|denizbank|tebbank|ing bank)\b",
    re.I,
)


def _hallucinated_entities(utterance: str, profile: Mapping[str, Any] | None) -> bool:
    """Heuristic: bank-like tokens in profile that are absent from utterance."""
    if not profile:
        return False
    utt = utterance.lower()
    blob = json.dumps(profile, ensure_ascii=False)
    for m in _BANKISH.finditer(blob):
        token = m.group(0).lower()
        if token not in utt:
            return True
    return False


async def call_raw(
    client: httpx.AsyncClient,
    *,
    base_url: str,
    model: str,
    utterance: str,
    locale: str,
    timeout_ms: int,
    max_tokens: int,
    temperature: float,
) -> dict[str, Any]:
    url = f"{base_url.rstrip('/')}/v1/chat/completions"
    user_payload = {"utterance": utterance, "locale": locale}
    body = {
        "model": model,
        "temperature": temperature,
        "max_tokens": max_tokens,
        "stream": False,
        "response_format": {
            "type": "json_schema",
            "json_schema": {
                "name": "NeedProfile",
                "schema": _need_profile_schema(),
                "strict": True,
            },
        },
        "messages": [
            {"role": "system", "content": _DEFAULT_SYSTEM_PROMPT},
            {"role": "user", "content": json.dumps(user_payload, ensure_ascii=False)},
        ],
    }
    started = time.perf_counter()
    try:
        resp = await client.post(
            url, json=body, timeout=max(timeout_ms / 1000.0, 0.5)
        )
        latency_ms = (time.perf_counter() - started) * 1000.0
        resp.raise_for_status()
        payload = resp.json()
    except httpx.TimeoutException:
        return {
            "ok": False,
            "timeout": True,
            "truncated": False,
            "latency_ms": (time.perf_counter() - started) * 1000.0,
            "raw_response": "",
            "http_error": "TIMEOUT",
            "payload": None,
        }
    except httpx.HTTPError as exc:
        return {
            "ok": False,
            "timeout": False,
            "truncated": False,
            "latency_ms": (time.perf_counter() - started) * 1000.0,
            "raw_response": "",
            "http_error": str(exc),
            "payload": None,
        }

    content = ""
    finish = None
    usage = None
    try:
        choice0 = (payload.get("choices") or [{}])[0]
        finish = choice0.get("finish_reason")
        msg = choice0.get("message") or {}
        content = msg.get("content") or ""
        usage = payload.get("usage")
    except Exception:  # noqa: BLE001
        content = ""

    truncated = finish == "length"
    return {
        "ok": True,
        "timeout": False,
        "truncated": truncated,
        "latency_ms": latency_ms,
        "raw_response": content if isinstance(content, str) else "",
        "http_error": None,
        "finish_reason": finish,
        "usage": usage,
        "payload": payload,
    }


def freeze_meta(args: argparse.Namespace, out_dir: Path) -> dict[str, Any]:
    """Freeze serving knobs for baseline. max_tokens MUST stay 512 for this experiment."""
    if int(args.max_tokens) != 512:
        raise SystemExit(
            f"P17-V3-RESIDUAL-001 freezes max_tokens=512 (got {args.max_tokens}). "
            "Use a separate experiment_id for 96–128 runtime trials."
        )
    meta = {
        "experiment_id": EXPERIMENT_ID,
        "started_at": _utc_now(),
        "created_at": _utc_now(),
        "campaign_gate": "CLOSED",
        "quality_claim": False,
        "quant_assignable": False,
        "dataset": str(args.dataset),
        "dataset_id": Path(args.dataset).name,
        "dataset_row_count_expected": 179,
        "split": "dev",
        "checkpoint_adapter": args.adapter,
        "adapter_path": args.adapter,
        "base_model": args.base_model,
        "base_gguf": args.base_gguf,
        "base_gguf_path": args.base_gguf,
        "lora_gguf": args.lora_gguf,
        "quant_tier": args.quant_tier,
        "prompt_id": PROMPT_VERSION,
        "prompt_version": PROMPT_VERSION,
        "schema_id": SCHEMA_VERSION,
        "schema_version": SCHEMA_VERSION,
        "system_prompt_fingerprint": "remote._DEFAULT_SYSTEM_PROMPT",
        "server_flags": args.server_flags,
        "llama_server_flags": args.server_flags,
        "threads": args.threads,
        "max_tokens": 512,
        "max_tokens_policy": (
            "FROZEN at 512 for baseline attribution. "
            "96–128 belongs to a separate runtime-gate experiment_id."
        ),
        "temperature": args.temperature,
        "timeout_ms": args.timeout_ms,
        "warmup_count": 3,
        "eval_port": args.port,
        "server_port": args.port,
        "eval_base_url": args.base_url,
        "model_alias": args.model,
        "server_alias": args.model,
        "runner_command": (
            "PYTHONPATH=src .venv/bin/python -u evaluation/p17_v3_residual_export.py"
        ),
        "pid": os.getpid(),
        "previous_failed_start": "var/run missing (ops note only; not an experiment result)",
        "note": "P17.1 residual — QUANT not assignable; no Campaign change",
    }
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / "experiment_meta.json"
    path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return meta


def compute_metrics(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    pos_tp = pos_fp = pos_fn = 0
    neg_tp = neg_fp = neg_fn = 0
    corr_tp = corr_fp = corr_fn = 0
    schema_ok = 0
    forbidden = 0
    conflict = 0
    over_n = 0
    empty_correct = 0
    empty_total = 0

    for r in rows:
        gold = r["gold"]
        pred = r["parsed_response"]
        g_pos, g_neg, g_corr = _sets_from_view(gold)
        p_pos, p_neg, _ = _sets_from_view(pred)
        p_corr = _pred_corr_set(pred)
        if g_corr == {"__correction_present__"}:
            g_corr = {"__correction_present__"} if gold.get("correction") else set()
            p_corr = {"__correction_present__"} if pred.get("correction") else set()

        pos_tp += len(p_pos & g_pos)
        pos_fp += len(p_pos - g_pos)
        pos_fn += len(g_pos - p_pos)
        neg_tp += len(p_neg & g_neg)
        neg_fp += len(p_neg - g_neg)
        neg_fn += len(g_neg - p_neg)
        corr_tp += len(p_corr & g_corr)
        corr_fp += len(p_corr - g_corr)
        corr_fn += len(g_corr - p_corr)

        if r.get("schema_valid"):
            schema_ok += 1
        if r.get("flags", {}).get("forbidden"):
            forbidden += 1
        if r.get("flags", {}).get("conflict"):
            conflict += 1
        if r.get("flags", {}).get("over_extract"):
            over_n += 1

        gold_empty = not (g_pos or g_neg or g_corr)
        pred_empty = not (p_pos or p_neg or p_corr)
        if gold_empty:
            empty_total += 1
            if pred_empty:
                empty_correct += 1

    def _r(n: int, d: int) -> float:
        return float(n) / float(d) if d else 0.0

    n = len(rows) or 1
    return {
        "case_count": len(rows),
        "pos_recall": _r(pos_tp, pos_tp + pos_fn),
        "pos_precision": _r(pos_tp, pos_tp + pos_fp),
        "neg_recall": _r(neg_tp, neg_tp + neg_fn),
        "neg_precision": _r(neg_tp, neg_tp + neg_fp),
        "corr_recall": _r(corr_tp, corr_tp + corr_fn),
        "corr_precision": _r(corr_tp, corr_tp + corr_fp),
        "false_positive_rate": _r(pos_fp + neg_fp, pos_tp + neg_tp + pos_fp + neg_fp),
        "false_negative_rate": _r(pos_fn + neg_fn, pos_tp + neg_tp + pos_fn + neg_fn),
        "schema_validity": _r(schema_ok, len(rows)),
        "forbidden_count": forbidden,
        "conflict_count": conflict,
        "over_extraction_rate": _r(over_n, len(rows)),
        "empty_extraction_accuracy": _r(empty_correct, empty_total),
        "latency_p50_ms": _percentile([r["latency_ms"] for r in rows], 50),
        "latency_p95_ms": _percentile([r["latency_ms"] for r in rows], 95),
    }


def _percentile(vals: Sequence[float], p: float) -> float:
    if not vals:
        return 0.0
    s = sorted(vals)
    if len(s) == 1:
        return float(s[0])
    k = (len(s) - 1) * (p / 100.0)
    f = int(k)
    c = min(f + 1, len(s) - 1)
    if f == c:
        return float(s[f])
    return float(s[f] + (s[c] - s[f]) * (k - f))


def build_failure_patterns(rows: Sequence[Mapping[str, Any]]) -> dict[str, Any]:
    by_cause: dict[str, dict[str, Any]] = {}
    cause_counter: Counter[str] = Counter()
    pattern_counter: Counter[str] = Counter()
    pattern_examples: dict[str, list[str]] = defaultdict(list)

    for r in rows:
        primary = r.get("primary_cause")
        if primary:
            cause_counter[primary] += 1
            entry = by_cause.setdefault(
                primary, {"count": 0, "patterns": [], "example_ids": []}
            )
            entry["count"] += 1
            for pat in r.get("linguistic_patterns") or []:
                if pat not in entry["patterns"]:
                    entry["patterns"].append(pat)
            if len(entry["example_ids"]) < 8:
                entry["example_ids"].append(r["utterance_id"])
        for pat in r.get("linguistic_patterns") or ["OTHER"]:
            pattern_counter[pat] += 1
            if len(pattern_examples[pat]) < 5:
                pattern_examples[pat].append(
                    {"utterance_id": r["utterance_id"], "utterance": r["utterance"]}
                )

    # Controlled v4 target mix from failure-weighted patterns (not one pattern inflated).
    fail_rows = [r for r in rows if r.get("primary_cause")]
    fail_pat: Counter[str] = Counter()
    for r in fail_rows:
        pats = r.get("linguistic_patterns") or ["OTHER"]
        for p in pats:
            fail_pat[p] += 1
    total_fail_pat = sum(fail_pat.values()) or 1
    v4_budget = 2000
    v4_targets = {}
    for pat in list(PATTERN_IDS) + ["OTHER"]:
        share = fail_pat.get(pat, 0) / total_fail_pat
        # floor for known hard patterns even if rare in this draft dev set
        base = max(40, int(round(share * v4_budget))) if fail_pat.get(pat) else 0
        if pat in ("NEG_SIMPLE", "CORRECTION_X_NOT_Y") and base == 0:
            base = 80  # ensure coverage for known HR failure modes
        if base:
            v4_targets[pat] = base
    # renormalize lightly if over budget
    s = sum(v4_targets.values()) or 1
    if s > v4_budget:
        v4_targets = {k: max(20, int(v * v4_budget / s)) for k, v in v4_targets.items()}

    return {
        "by_primary_cause": by_cause,
        "cause_counts": dict(cause_counter),
        "linguistic_pattern_counts": dict(pattern_counter),
        "linguistic_pattern_examples": dict(pattern_examples),
        "failing_pattern_counts": dict(fail_pat),
        "v4_target_example_counts": v4_targets,
        "v4_target_notes": (
            "Targets derived from failing residual pattern distribution with "
            "minimum floors for known hard patterns; not a single-pattern inflate."
        ),
        "decision_hint": _decision_hint(cause_counter),
    }


def _decision_hint(cause_counter: Counter[str]) -> dict[str, Any]:
    model_n = sum(v for k, v in cause_counter.items() if k.startswith("MODEL_"))
    runtime_n = sum(v for k, v in cause_counter.items() if k.startswith("RUNTIME_"))
    matcher_n = cause_counter.get("MATCHER", 0)
    if model_n >= runtime_n and model_n >= matcher_n:
        next_step = "targeted_v4_sft"
    elif runtime_n > model_n:
        next_step = "serving_schema_decode_fix"
    elif matcher_n > model_n:
        next_step = "matcher_backlog_only"
    else:
        next_step = "targeted_v4_sft"
    return {
        "model_weighted": model_n,
        "runtime_weighted": runtime_n,
        "matcher_weighted": matcher_n,
        "quant": "not_decidable_until_same_checkpoint_matrix",
        "next_step": next_step,
    }


async def run(args: argparse.Namespace) -> int:
    out_dir = Path(args.out_dir)
    meta = freeze_meta(args, out_dir)
    print(json.dumps({"meta_written": True, **{k: meta[k] for k in (
        "experiment_id", "eval_port", "model_alias", "quant_tier", "max_tokens"
    )}}, ensure_ascii=False), flush=True)

    dataset = load_jsonl(Path(args.dataset))
    cases = list(dataset.cases)
    if args.limit:
        cases = cases[: args.limit]
    print(f"[P17.1] cases={len(cases)} url={args.base_url} model={args.model}", flush=True)

    # Warmup
    async with httpx.AsyncClient() as client:
        for _ in range(3):
            await call_raw(
                client,
                base_url=args.base_url,
                model=args.model,
                utterance="tablet bakıyorum",
                locale="tr-TR",
                timeout_ms=args.timeout_ms,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
            )

        det = DeterministicFastExtractor()
        rows: list[dict[str, Any]] = []

        for idx, case in enumerate(cases, start=1):
            gold = _gold_view(case.semantic_constraints or {})
            raw_pack = await call_raw(
                client,
                base_url=args.base_url,
                model=args.model,
                utterance=case.utterance,
                locale=case.locale or "tr-TR",
                timeout_ms=args.timeout_ms,
                max_tokens=args.max_tokens,
                temperature=args.temperature,
            )

            raw = raw_pack.get("raw_response") or ""
            timeout = bool(raw_pack.get("timeout"))
            truncated = bool(raw_pack.get("truncated"))
            non_json = False
            schema_valid = False
            parsed_profile: Optional[dict[str, Any]] = None
            status = "ok"

            if timeout:
                status = "TIMEOUT"
            elif raw_pack.get("http_error") and not raw:
                status = "PROVIDER_ERROR"
            elif not raw.strip():
                status = "EMPTY_OUTPUT"
                non_json = True
            else:
                try:
                    parsed_profile = json.loads(raw)
                    if not isinstance(parsed_profile, dict):
                        non_json = True
                        status = "INVALID_SCHEMA"
                    else:
                        # lightweight schema: require intent + semantic_constraints keys
                        if "intent" in parsed_profile and "semantic_constraints" in parsed_profile:
                            schema_valid = True
                        else:
                            schema_valid = False
                            status = "INVALID_SCHEMA"
                except json.JSONDecodeError:
                    non_json = True
                    status = "INVALID_SCHEMA"
                    if truncated:
                        status = "TRUNCATED"

            forbidden_hits = _scan_forbidden_leaves(parsed_profile) if parsed_profile else []
            forbidden_field = bool(forbidden_hits)
            hallucinated = _hallucinated_entities(case.utterance, parsed_profile)

            parsed_view = _pred_view_from_profile(parsed_profile)

            # Evaluated = hybrid(model bag, deterministic) — for MATCHER attribution only
            model_bag = {
                "positive": [{"concept": c} for c in parsed_view["positive"]],
                "negative": [{"concept": c} for c in parsed_view["negative"]],
                "corrections": list(parsed_view.get("corrections") or []),
            }
            try:
                det_out = await det.extract(case.utterance, locale=case.locale or "tr-TR")
                det_bag = det_out.constraints.to_matcher_dict() if det_out.constraints else {}
            except Exception:  # noqa: BLE001
                det_bag = {}
            hybrid = hybrid_final_constraints(
                model_constraints=model_bag,
                deterministic_constraints=det_bag,
            )
            evaluated_view = _pred_view_from_bag(hybrid)

            primary, secondary, flags = assign_causes(
                status=status,
                gold=gold,
                pred=parsed_view,
                schema_valid=schema_valid and status == "ok",
                forbidden_field=forbidden_field,
                hallucinated=hallucinated,
                truncated=truncated or status == "TRUNCATED",
                timeout=timeout,
                non_json=non_json,
            )

            # MATCHER only if model OK vs gold but we still mark nothing as residual fail.
            # Downstream mismatch is recorded in notes, not as model primary.
            if flags.get("model_ok"):
                primary = None
                secondary = []

            patterns = linguistic_patterns(case.utterance)
            row = {
                "experiment_id": EXPERIMENT_ID,
                "utterance_id": case.case_id,
                "utterance": case.utterance,
                "locale": case.locale or "tr-TR",
                "gold": gold,
                "raw_response": raw,
                "parsed_response": parsed_view,
                "evaluated_response": evaluated_view,
                "need_profile": parsed_profile,
                "schema_valid": bool(schema_valid and not non_json and status == "ok"),
                "truncated": truncated or status == "TRUNCATED",
                "timeout": timeout,
                "non_json": non_json,
                "status": status,
                "latency_ms": float(raw_pack.get("latency_ms") or 0.0),
                "finish_reason": raw_pack.get("finish_reason"),
                "usage": raw_pack.get("usage"),
                "forbidden_hits": forbidden_hits,
                "linguistic_patterns": patterns,
                "primary_cause": primary,
                "secondary_causes": secondary,
                "flags": flags,
                "review_notes": "",
            }
            rows.append(row)
            if idx % 10 == 0 or idx == len(cases):
                print(
                    f"[P17.1] {idx}/{len(cases)} id={case.case_id} "
                    f"status={status} primary={primary} lat_ms={round(row['latency_ms'])}",
                    flush=True,
                )

    # Artifacts
    raw_path = out_dir / "residual_raw.jsonl"
    with raw_path.open("w", encoding="utf-8") as fh:
        for r in rows:
            fh.write(json.dumps(r, ensure_ascii=False) + "\n")

    csv_path = out_dir / "residual_review.csv"
    with csv_path.open("w", encoding="utf-8", newline="") as fh:
        w = csv.DictWriter(
            fh,
            fieldnames=[
                "utterance_id",
                "utterance",
                "gold_positive",
                "pred_positive",
                "gold_negative",
                "pred_negative",
                "gold_correction",
                "pred_correction",
                "schema_valid",
                "forbidden",
                "conflict",
                "primary_cause",
                "secondary_causes",
                "linguistic_patterns",
                "review_notes",
            ],
        )
        w.writeheader()
        for r in rows:
            w.writerow(
                {
                    "utterance_id": r["utterance_id"],
                    "utterance": r["utterance"],
                    "gold_positive": "|".join(r["gold"]["positive"]),
                    "pred_positive": "|".join(r["parsed_response"]["positive"]),
                    "gold_negative": "|".join(r["gold"]["negative"]),
                    "pred_negative": "|".join(r["parsed_response"]["negative"]),
                    "gold_correction": r["gold"]["correction"],
                    "pred_correction": r["parsed_response"]["correction"],
                    "schema_valid": r["schema_valid"],
                    "forbidden": r["flags"].get("forbidden", False),
                    "conflict": r["flags"].get("conflict", False),
                    "primary_cause": r["primary_cause"] or "",
                    "secondary_causes": "|".join(r["secondary_causes"]),
                    "linguistic_patterns": "|".join(r["linguistic_patterns"]),
                    "review_notes": r["review_notes"],
                }
            )

    metrics = compute_metrics(rows)
    metrics_path = out_dir / "metrics.json"
    metrics_path.write_text(
        json.dumps(metrics, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    patterns_doc = build_failure_patterns(rows)
    patterns_path = out_dir / "failure_patterns.json"
    patterns_path.write_text(
        json.dumps(patterns_doc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )

    summary = {
        "experiment_id": EXPERIMENT_ID,
        "cases": len(rows),
        "failing": sum(1 for r in rows if r.get("primary_cause")),
        "metrics": metrics,
        "decision_hint": patterns_doc["decision_hint"],
        "v4_target_example_counts": patterns_doc["v4_target_example_counts"],
        "artifacts": {
            "experiment_meta": str(out_dir / "experiment_meta.json"),
            "residual_raw": str(raw_path),
            "residual_review": str(csv_path),
            "metrics": str(metrics_path),
            "failure_patterns": str(patterns_path),
        },
        "campaign_gate": "CLOSED",
    }
    (out_dir / "p17_1_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2), flush=True)
    return 0


def main() -> None:
    p = argparse.ArgumentParser(description="P17.1 V3 residual export")
    p.add_argument("--dataset", type=Path, default=DEFAULT_DEV)
    p.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    p.add_argument("--base-url", default=os.environ.get("FAST_C_BASE_URL", "http://127.0.0.1:8026"))
    p.add_argument(
        "--model",
        default=os.environ.get("FAST_C_MODEL_REFERENCE", "poc-fast-nine-b-lora-v3"),
    )
    p.add_argument("--port", type=int, default=8026)
    p.add_argument("--max-tokens", type=int, default=int(os.environ.get("FAST_C_MAX_OUTPUT_TOKENS", "512")))
    p.add_argument("--temperature", type=float, default=0.0)
    p.add_argument("--timeout-ms", type=int, default=120000)
    p.add_argument("--threads", type=int, default=16)
    p.add_argument("--quant-tier", default="Q4_K_M")
    p.add_argument(
        "--base-model",
        default="Qwen/Qwen3.5-9B",
    )
    p.add_argument(
        "--base-gguf",
        default="/data/nanobaseai/models/taksitlio-fast-c/Qwen_Qwen3.5-9B-Q4_K_M.gguf",
    )
    p.add_argument(
        "--lora-gguf",
        default="training/exports/lora-out-9b-v3-cpu/gguf/need-profile-lora-f16.gguf",
    )
    p.add_argument(
        "--adapter",
        default="training/exports/lora-out-9b-v3-cpu/adapter",
    )
    p.add_argument(
        "--server-flags",
        default=(
            "-c 8192 -t 16 -b 512 -ub 256 -np 2 "
            "--cache-type-k q4_0 --cache-type-v q4_0 --jinja --reasoning off --metrics"
        ),
    )
    p.add_argument("--limit", type=int, default=None)
    args = p.parse_args()
    raise SystemExit(asyncio.run(run(args)))


if __name__ == "__main__":
    main()
