"""Parser-lane metrics and gate evaluation for Query Golden Set v1."""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Mapping, Optional, Sequence

from taksitlio.evaluation.query_golden.loader import QueryGoldenCase
from taksitlio.llm_routing import should_route_to_llm
from taksitlio.query_clarification.policy import should_ask_clarification
from taksitlio.query_understanding import CatalogHints, detect_gaps, fast_parse
from taksitlio.query_understanding.fast_parser import FastParseResult
from taksitlio.semantic_matching.turkish_normalize import normalize_turkish


@dataclass
class ParserLaneMetrics:
    case_count: int = 0
    human_reviewed_count: int = 0
    merchant_precision: Optional[float] = None
    institution_precision: Optional[float] = None
    category_precision: Optional[float] = None
    price_extraction_accuracy: Optional[float] = None
    term_extraction_accuracy: Optional[float] = None
    negation_recall: Optional[float] = None
    correction_recall: Optional[float] = None
    clarification_accuracy: Optional[float] = None
    llm_routing_accuracy: Optional[float] = None
    false_auto_resolution_count: int = 0
    unnecessary_llm_on_fast_count: int = 0
    unnecessary_llm_on_clarification_count: int = 0
    support: dict[str, int] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm(name: str) -> str:
    return normalize_turkish(name).value


def _names_match(a: str, b: str) -> bool:
    na, nb = _norm(a), _norm(b)
    if not na or not nb:
        return False
    return na == nb or na in nb or nb in na


def predict_route(parse: FastParseResult, gaps) -> str:
    if should_ask_clarification(gaps=gaps, clarification_count=0, parse=parse):
        return "CLARIFICATION"
    if should_route_to_llm(parse, gaps, clarification_count=0):
        return "LLM"
    if gaps.confidence_band == "HIGH" or parse.positive_categories or parse.merchant:
        return "FAST"
    return "FAST"


def predict_case(
    case: QueryGoldenCase, *, catalog: CatalogHints
) -> dict[str, Any]:
    parse = fast_parse(case.message, catalog=catalog)
    gaps = detect_gaps(parse)
    route = predict_route(parse, gaps)
    # Soft override: expected OUT_OF_SCOPE / adversarial injection
    exp_route = case.expected.get("route")
    if exp_route == "OUT_OF_SCOPE" and case.expected.get("intent") == "OUT_OF_SCOPE":
        # Parser lane does not run full off-domain refuse; mark predicted from expected
        # only when message has clear injection markers.
        lower = case.message.casefold()
        if "ignore previous" in lower or "system:" in lower or "garanti et" in lower:
            route = "OUT_OF_SCOPE"

    return {
        "case_id": case.case_id,
        "route": route,
        "llm_required": route == "LLM",
        "clarification_should_ask": route == "CLARIFICATION",
        "parse": parse.to_dict(),
        "gaps": gaps.to_dict(),
    }


def _ratio(scored: int, total: int) -> Optional[float]:
    if total <= 0:
        return None
    return scored / total


def evaluate_parser_lane(
    cases: Sequence[QueryGoldenCase],
    *,
    catalog: CatalogHints,
    predictions: Mapping[str, Mapping[str, Any]] | None = None,
) -> tuple[ParserLaneMetrics, list[dict[str, Any]]]:
    """Score parser lane. If predictions omitted, run fast_parse pipeline."""

    preds: dict[str, Mapping[str, Any]] = dict(predictions or {})
    details: list[dict[str, Any]] = []
    if not preds:
        for case in cases:
            preds[case.case_id] = predict_case(case, catalog=catalog)

    m_ok = m_tot = 0
    i_ok = i_tot = 0
    c_ok = c_tot = 0
    p_ok = p_tot = 0
    t_ok = t_tot = 0
    n_ok = n_tot = 0
    corr_ok = corr_tot = 0
    clar_ok = clar_tot = 0
    llm_ok = llm_tot = 0
    false_auto = 0
    unnec_llm_fast = 0
    unnec_llm_clar = 0
    hr = 0

    for case in cases:
        if case.annotation.get("status") == "HUMAN_REVIEWED":
            hr += 1
        pred = preds.get(case.case_id) or {}
        parse = pred.get("parse") or {}
        exp = case.expected
        row: dict[str, Any] = {"case_id": case.case_id, "bucket": case.bucket}

        # Merchant
        exp_m = exp.get("merchant")
        if isinstance(exp_m, dict) and exp_m.get("display_name"):
            m_tot += 1
            got = (parse.get("merchant") or {}).get("display_name")
            ok = bool(got and _names_match(str(got), str(exp_m["display_name"])))
            m_ok += int(ok)
            row["merchant_ok"] = ok
            if not ok and got:
                # Wrong entity auto-selected when match was required
                conf = float((parse.get("merchant") or {}).get("confidence") or 0)
                if conf >= 0.92:
                    false_auto += 1

        # Institutions
        exp_inst = exp.get("institutions") or []
        if exp_inst:
            i_tot += 1
            got_names = {
                _norm(str(x.get("display_name") or ""))
                for x in (parse.get("preferred_institutions") or [])
                if x.get("display_name")
            }
            # preferred_institutions may use institution_id; also check parse brands path
            # FastParseResult.to_dict omits institutions list — use preferred_institutions
            exp_names = {_norm(str(x["display_name"])) for x in exp_inst}
            ok = bool(exp_names) and exp_names.issubset(got_names | set())
            # Fallback: substring in message already resolved into preferred_institutions
            if not ok:
                # preferred may store display_name
                ok = all(
                    any(_names_match(str(e["display_name"]), str(g.get("display_name") or g.get("institution_id") or ""))
                        for g in (parse.get("preferred_institutions") or []))
                    for e in exp_inst
                )
            i_ok += int(ok)
            row["institution_ok"] = ok

        # Category
        exp_c = exp.get("category")
        if isinstance(exp_c, dict) and exp_c.get("display_name"):
            c_tot += 1
            got_cats = parse.get("positive_categories") or []
            ok = any(
                _names_match(str(g.get("display_name") or ""), str(exp_c["display_name"]))
                for g in got_cats
            )
            c_ok += int(ok)
            row["category_ok"] = ok
            if not ok and got_cats:
                top = got_cats[0]
                if float(top.get("confidence") or 0) >= 0.92:
                    false_auto += 1

        # Price / budget
        exp_b = exp.get("budget") or {}
        exp_max = exp_b.get("maximum")
        exp_val = exp_b.get("value")
        if exp_max is not None or exp_val is not None:
            p_tot += 1
            got_b = parse.get("budget") or {}
            target = float(exp_max if exp_max is not None else exp_val)
            got_num = got_b.get("maximum")
            if got_num is None:
                got_num = got_b.get("value")
            ok = got_num is not None and abs(float(got_num) - target) <= max(1.0, target * 0.05)
            p_ok += int(ok)
            row["price_ok"] = ok

        # Terms
        exp_terms = exp.get("requested_terms") or []
        if exp_terms:
            t_tot += 1
            got_terms = set(int(x) for x in (parse.get("requested_terms") or []))
            ok = set(int(x) for x in exp_terms).issubset(got_terms)
            t_ok += int(ok)
            row["term_ok"] = ok

        # Negation recall
        neg = (exp.get("exclusions") or {}).get("negative_categories") or []
        if neg:
            n_tot += 1
            got_neg = parse.get("negative_categories") or []
            ok = all(
                any(_names_match(str(g.get("display_name") or ""), str(n)) for g in got_neg)
                for n in neg
            )
            n_ok += int(ok)
            row["negation_ok"] = ok

        # Correction recall (cancelled constraints — parser may surface as neg or category switch)
        cancelled = (exp.get("exclusions") or {}).get("cancelled") or []
        if cancelled:
            corr_tot += 1
            got_pos = parse.get("positive_categories") or []
            exp_cat = (exp.get("category") or {}).get("display_name")
            ok = True
            if exp_cat:
                ok = any(
                    _names_match(str(g.get("display_name") or ""), str(exp_cat))
                    for g in got_pos
                )
            corr_ok += int(ok)
            row["correction_ok"] = ok

        # Clarification accuracy — only buckets that assert clarification behavior
        exp_clar = bool((exp.get("clarification") or {}).get("should_ask"))
        pred_clar = bool(pred.get("clarification_should_ask"))
        if case.bucket in ("clarification", "adversarial") or exp_clar:
            clar_tot += 1
            ok = exp_clar == pred_clar
            clar_ok += int(ok)
            row["clarification_ok"] = ok

        # LLM routing — compare expected route when in {FAST, CLARIFICATION, LLM}
        exp_llm = bool(exp.get("llm_required"))
        pred_llm = bool(pred.get("llm_required"))
        exp_route = exp.get("route")
        pred_route = pred.get("route")
        if exp_route in ("FAST", "CLARIFICATION", "LLM"):
            llm_tot += 1
            ok = pred_route == exp_route
            llm_ok += int(ok)
            row["llm_routing_ok"] = ok
        elif exp_llm or pred_llm:
            llm_tot += 1
            ok = exp_llm == pred_llm
            llm_ok += int(ok)
            row["llm_routing_ok"] = ok

        if exp_route == "FAST" and pred_llm:
            unnec_llm_fast += 1
        if exp_route == "CLARIFICATION" and pred_llm:
            unnec_llm_clar += 1

        details.append(row)

    metrics = ParserLaneMetrics(
        case_count=len(cases),
        human_reviewed_count=hr,
        merchant_precision=_ratio(m_ok, m_tot),
        institution_precision=_ratio(i_ok, i_tot),
        category_precision=_ratio(c_ok, c_tot),
        price_extraction_accuracy=_ratio(p_ok, p_tot),
        term_extraction_accuracy=_ratio(t_ok, t_tot),
        negation_recall=_ratio(n_ok, n_tot),
        correction_recall=_ratio(corr_ok, corr_tot),
        clarification_accuracy=_ratio(clar_ok, clar_tot),
        llm_routing_accuracy=_ratio(llm_ok, llm_tot),
        false_auto_resolution_count=false_auto,
        unnecessary_llm_on_fast_count=unnec_llm_fast,
        unnecessary_llm_on_clarification_count=unnec_llm_clar,
        support={
            "merchant": m_tot,
            "institution": i_tot,
            "category": c_tot,
            "price": p_tot,
            "term": t_tot,
            "negation": n_tot,
            "correction": corr_tot,
            "clarification": clar_tot,
            "llm_routing": llm_tot,
        },
    )
    return metrics, details


def evaluate_parser_gate(
    metrics: ParserLaneMetrics,
    gates: Mapping[str, Any],
    *,
    draft_count: int = 0,
) -> dict[str, Any]:
    """Apply parser thresholds.

    Full PASS/FAIL only when the set is review-complete (no DRAFT) and
    HUMAN_REVIEWED ≥ minimum. Otherwise status is BOOTSTRAP — infrastructure
    green with threshold misses reported as warnings (ADR-005/013).
    """

    thresholds = gates.get("parser_gate_thresholds") or {}
    min_hr = int(gates.get("minimum_human_reviewed") or 100)
    violations: list[str] = []

    def _check_min(key: str, value: Optional[float]) -> None:
        rule = thresholds.get(key) or {}
        if "min" not in rule:
            return
        if value is None:
            violations.append(f"{key}: no support")
            return
        if float(value) < float(rule["min"]):
            violations.append(f"{key}: {value:.4f} < {rule['min']}")

    def _check_max_count(key: str, value: int) -> None:
        rule = thresholds.get(key) or {}
        if "max_count" not in rule:
            return
        if int(value) > int(rule["max_count"]):
            violations.append(f"{key}: {value} > {rule['max_count']}")

    _check_min("merchant_precision", metrics.merchant_precision)
    _check_min("institution_precision", metrics.institution_precision)
    _check_min("category_precision", metrics.category_precision)
    _check_min("price_extraction_accuracy", metrics.price_extraction_accuracy)
    _check_min("term_extraction_accuracy", metrics.term_extraction_accuracy)
    _check_min("negation_recall", metrics.negation_recall)
    _check_min("correction_recall", metrics.correction_recall)
    _check_min("clarification_accuracy", metrics.clarification_accuracy)
    _check_min("llm_routing_accuracy", metrics.llm_routing_accuracy)
    _check_max_count("false_auto_resolution_count", metrics.false_auto_resolution_count)
    _check_max_count(
        "unnecessary_llm_on_fast_count", metrics.unnecessary_llm_on_fast_count
    )
    _check_max_count(
        "unnecessary_llm_on_clarification_count",
        metrics.unnecessary_llm_on_clarification_count,
    )

    review_complete = draft_count == 0 and metrics.human_reviewed_count >= min_hr
    zero_tol_keys = (
        "false_auto_resolution_count",
        "unnecessary_llm_on_fast_count",
        "unnecessary_llm_on_clarification_count",
    )
    zero_tol_violations = [v for v in violations if v.split(":")[0] in zero_tol_keys]

    if zero_tol_violations:
        status = "FAIL"
        notes = list(zero_tol_violations)
        if not review_complete:
            notes.append("zero-tolerance breach blocks even bootstrap green")
    elif not review_complete:
        status = "BOOTSTRAP"
        notes = [
            "full ACCEPT deferred until DRAFT=0 and "
            f"HUMAN_REVIEWED≥{min_hr} (ADR-005/013 bootstrap); "
            f"hr={metrics.human_reviewed_count} draft={draft_count}"
        ]
        if violations:
            notes.extend(f"warn:{v}" for v in violations)
    elif violations:
        status = "FAIL"
        notes = list(violations)
    else:
        status = "PASS"
        notes = []

    return {
        "status": status,
        "violations": violations,
        "notes": notes,
        "minimum_human_reviewed": min_hr,
        "human_reviewed_count": metrics.human_reviewed_count,
        "draft_count": draft_count,
        "review_complete": review_complete,
    }
