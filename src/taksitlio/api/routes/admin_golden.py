"""Admin golden dual-control review API.

Write paths require INTERNAL token auth, audit history, and optimistic locking.
PREPARER cannot APPROVE. System response must never be copied into expected.
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, Header, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from taksitlio.api.deps import container_from

router = APIRouter(tags=["admin-golden"])

FORBIDDEN_FINANCE_INVARIANTS = [
    "bank claim",
    "campaign claim",
    "monthly payment",
    "total repayment",
    "installment term",
    "zero-rate claim",
    "bank claim yasak",
    "campaign claim yasak",
    "monthly payment yasak",
    "total repayment yasak",
    "term claim yasak",
    "zero-rate claim yasak",
]


def _require_admin_token(
    x_taksitlio_internal_token: Optional[str] = None,
    x_admin_token: Optional[str] = None,
) -> str:
    expected = (os.environ.get("TAKSITLIO_INTERNAL_TOKEN") or "").strip()
    presented = (x_taksitlio_internal_token or x_admin_token or "").strip()
    if not expected:
        raise HTTPException(status_code=503, detail="admin token not configured")
    if presented != expected:
        raise HTTPException(status_code=403, detail="unauthorized admin golden write")
    return presented


def _pool(request: Request):
    container = container_from(request)
    pool = container.extras.get("db_pool") or container.extras.get("pg_pool")
    if pool is None:
        raise HTTPException(status_code=501, detail="db_pool not configured")
    return pool


def _parse_json(val: Any) -> Any:
    if isinstance(val, str):
        try:
            return json.loads(val)
        except Exception:  # noqa: BLE001
            return {}
    return val or {}


def _row_to_candidate(row: Any) -> dict[str, Any]:
    expected = _parse_json(row.get("expected"))
    constraints = _parse_json(row.get("expected_constraints"))
    return {
        "id": int(row["id"]),
        "case_id": row["case_id"],
        "query_text": row["query_text"],
        "lifecycle_status": row["lifecycle_status"],
        "prepared_by": row.get("prepared_by"),
        "prepared_at": row["prepared_at"].isoformat() if row.get("prepared_at") else None,
        "reviewed_by": row.get("reviewed_by"),
        "reviewed_at": row["reviewed_at"].isoformat() if row.get("reviewed_at") else None,
        "review_decision": row.get("review_decision"),
        "review_notes": row.get("review_notes"),
        "claimed_by": row.get("claimed_by"),
        "claimed_at": row["claimed_at"].isoformat() if row.get("claimed_at") else None,
        "row_version": int(row["row_version"]) if row.get("row_version") is not None else 1,
        "bucket": row.get("bucket") or expected.get("bucket") or row.get("source_signal"),
        "demand_weight": float(row["demand_weight"])
        if row.get("demand_weight") is not None
        else float(expected.get("demand_weight") or 1),
        "cohort_id": row.get("cohort_id") or expected.get("cohort_id"),
        "cohort_version": row.get("cohort_version") or expected.get("cohort_version"),
        "catalog_revision": row.get("catalog_revision"),
        "source_query_id": row.get("source_query_id") or expected.get("source_query_id"),
        "expected_route": row.get("expected_route"),
        "expected_entities": _parse_json(row.get("expected_entities")),
        "expected_constraints": constraints,
        "expected_clarification_behavior": expected.get("expected_clarification_behavior") or {},
        "allowed_product_invariants": expected.get("allowed_product_invariants") or [],
        "forbidden_product_invariants": expected.get("forbidden_product_invariants")
        or list(FORBIDDEN_FINANCE_INVARIANTS),
        "expected": expected,
    }


async def _audit(
    conn: Any,
    *,
    case_pk: int,
    case_id: str,
    action: str,
    actor: str,
    from_lifecycle: Optional[str],
    to_lifecycle: Optional[str],
    row_version_before: Optional[int],
    row_version_after: Optional[int],
    notes: Optional[str] = None,
    payload: Optional[dict[str, Any]] = None,
) -> None:
    exists = await conn.fetchval(
        """
        SELECT EXISTS (
          SELECT 1 FROM information_schema.tables
          WHERE table_name='continuous_golden_review_history'
        )
        """
    )
    if not exists:
        return
    await conn.execute(
        """
        INSERT INTO continuous_golden_review_history (
          case_pk, case_id, action, actor, from_lifecycle, to_lifecycle,
          row_version_before, row_version_after, notes, payload
        ) VALUES ($1,$2,$3,$4,$5,$6,$7,$8,$9,$10::jsonb)
        """,
        case_pk,
        case_id,
        action,
        actor,
        from_lifecycle,
        to_lifecycle,
        row_version_before,
        row_version_after,
        notes,
        json.dumps(payload or {}),
    )


def _merge_forbidden(extra: Optional[List[Any]]) -> list[str]:
    out: list[str] = []
    for inv in list(extra or []) + list(FORBIDDEN_FINANCE_INVARIANTS):
        s = str(inv).strip()
        if s and s not in out:
            out.append(s)
    return out


class GoldenClaimIn(BaseModel):
    actor: str = Field(..., min_length=1)
    row_version: int = Field(..., ge=1)


class GoldenPrepareIn(BaseModel):
    prepared_by: str = Field(..., min_length=1)
    row_version: int = Field(..., ge=1)
    expected_route: Optional[str] = None
    expected_entities: Dict[str, Any] = Field(default_factory=dict)
    expected_positive_constraints: List[Any] = Field(default_factory=list)
    expected_negative_constraints: List[Any] = Field(default_factory=list)
    expected_clarification_behavior: Dict[str, Any] = Field(default_factory=dict)
    allowed_product_invariants: List[Any] = Field(default_factory=list)
    forbidden_product_invariants: List[Any] = Field(default_factory=list)
    review_notes: str = Field(..., min_length=1)
    merchant_scope_codes: List[str] = Field(default_factory=list)
    category_scope_ids: List[str] = Field(default_factory=list)


class GoldenReviewIn(BaseModel):
    reviewed_by: str = Field(..., min_length=1)
    review_decision: str = Field(..., pattern="^(APPROVED|REJECTED|NEEDS_REVISION)$")
    review_notes: str = Field(..., min_length=1)
    row_version: int = Field(..., ge=1)
    expected_route: Optional[str] = None
    expected_entities: Dict[str, Any] = Field(default_factory=dict)
    expected_positive_constraints: List[Any] = Field(default_factory=list)
    expected_negative_constraints: List[Any] = Field(default_factory=list)
    expected_clarification_behavior: Dict[str, Any] = Field(default_factory=dict)
    allowed_product_invariants: List[Any] = Field(default_factory=list)
    forbidden_product_invariants: List[Any] = Field(default_factory=list)


REVIEW_HTML = """<!DOCTYPE html>
<html lang="tr"><head><meta charset="utf-8"/><title>Golden Review</title>
<style>
body{font-family:IBM Plex Sans,Segoe UI,sans-serif;background:#0f1419;color:#e8eef4;margin:0;padding:1rem}
.meta{color:#9aa7b5;font-size:.85rem}.warn{color:#f0c14d}
input,textarea,select,button{margin:.25rem 0;padding:.45rem;border-radius:6px;border:1px solid #334455;background:#101820;color:#e8eef4}
button{background:#3d9cf0;color:#041018;font-weight:600;cursor:pointer}
pre{background:#101820;padding:.75rem;max-height:180px;overflow:auto;font-size:.75rem}
</style></head><body>
<h1>Golden Dual-Control Review</h1>
<p class="sub meta">INTERNAL token required for writes · PREPARER ≠ REVIEWER · never copy system response into expected</p>
<label>Actor <input id="actor" placeholder="operator-id"/></label>
<label>INTERNAL token <input id="token" type="password"/></label>
<label>Status <select id="statusFilter"><option>REVIEW_REQUIRED</option><option>NEEDS_REVISION</option><option>APPROVED</option><option>REJECTED</option><option value="">ALL</option></select></label>
<button id="reload">Reload</button>
<div id="cases"></div><div id="detail"></div>
<script>
const apiBase=window.TAKSITLIO_ADMIN_API_BASE||'/v1/admin';
let selected=null;
function headers(){const t=document.getElementById('token').value.trim();return {'Content-Type':'application/json','X-Taksitlio-Internal-Token':t};}
async function loadCases(){
  const st=document.getElementById('statusFilter').value;
  const q=st?('?lifecycle_status='+encodeURIComponent(st)):'';
  const res=await fetch(apiBase+'/golden/candidates'+q); const data=await res.json();
  const box=document.getElementById('cases'); box.innerHTML='';
  (data.candidates||[]).forEach(c=>{
    const el=document.createElement('div');
    el.innerHTML=`<button type=button>${(c.query_text||'').slice(0,90)} · ${c.bucket} · ${c.lifecycle_status} · v${c.row_version||1}</button>`;
    el.onclick=()=>{selected=c; loadDetail(c.id);};
    box.appendChild(el);
  });
}
async function loadDetail(id){
  const res=await fetch(apiBase+'/golden/candidates/'+id); const c=await res.json(); selected=c;
  const hist=await (await fetch(apiBase+'/golden/candidates/'+id+'/history')).json();
  document.getElementById('detail').innerHTML=`
  <h2>${c.query_text||''}</h2>
  <div class="meta">id=${c.id} bucket=${c.bucket} cohort=${c.cohort_id}/${c.cohort_version} row_version=${c.row_version}</div>
  <p class="warn">System response is REFERENCE ONLY — do not paste into expected.</p>
  <label>expected_route <input id="route" value="${c.expected_route||''}"/></label>
  <label>expected_entities JSON<textarea id="ent">${JSON.stringify(c.expected_entities||{},null,2)}</textarea></label>
  <label>positive constraints JSON<textarea id="pos">${JSON.stringify((c.expected_constraints||{}).positive||[],null,2)}</textarea></label>
  <label>negative constraints JSON<textarea id="neg">${JSON.stringify((c.expected_constraints||{}).negative||[],null,2)}</textarea></label>
  <label>clarification JSON<textarea id="clar">${JSON.stringify(c.expected_clarification_behavior||{},null,2)}</textarea></label>
  <label>allowed invariants JSON<textarea id="allow">${JSON.stringify(c.allowed_product_invariants||[],null,2)}</textarea></label>
  <label>forbidden invariants JSON<textarea id="forbid">${JSON.stringify(c.forbidden_product_invariants||[],null,2)}</textarea></label>
  <label>notes<textarea id="notes">${c.review_notes||''}</textarea></label>
  <button onclick="claim()">Claim</button>
  <button onclick="prepare()">Save prepare</button>
  <button onclick="decide('APPROVED')">Approve</button>
  <button onclick="decide('NEEDS_REVISION')">Needs revision</button>
  <button onclick="decide('REJECTED')">Reject</button>
  <h3>History</h3><pre>${JSON.stringify(hist.history||[],null,2)}</pre>
  <h3>Cohort samples</h3><pre>${JSON.stringify(c.cohort_product_samples||[],null,2)}</pre>
  <h3>System reference</h3><pre>${JSON.stringify(c.system_response_reference||{},null,2)}</pre>`;
}
function j(id,fb){const r=document.getElementById(id).value.trim(); return r?JSON.parse(r):fb;}
async function claim(){
  const body={actor:document.getElementById('actor').value.trim(), row_version:selected.row_version};
  const res=await fetch(apiBase+'/golden/candidates/'+selected.id+'/claim',{method:'POST',headers:headers(),body:JSON.stringify(body)});
  alert(await res.text()); loadDetail(selected.id);
}
async function prepare(){
  const body={
    prepared_by:document.getElementById('actor').value.trim(),
    row_version:selected.row_version,
    expected_route:document.getElementById('route').value.trim()||null,
    expected_entities:j('ent',{}),
    expected_positive_constraints:j('pos',[]),
    expected_negative_constraints:j('neg',[]),
    expected_clarification_behavior:j('clar',{}),
    allowed_product_invariants:j('allow',[]),
    forbidden_product_invariants:j('forbid',[]),
    review_notes:document.getElementById('notes').value.trim()
  };
  const res=await fetch(apiBase+'/golden/candidates/'+selected.id+'/prepare',{method:'POST',headers:headers(),body:JSON.stringify(body)});
  alert(await res.text()); loadDetail(selected.id);
}
async function decide(decision){
  const body={
    reviewed_by:document.getElementById('actor').value.trim(),
    review_decision:decision,
    review_notes:document.getElementById('notes').value.trim(),
    row_version:selected.row_version,
    expected_route:document.getElementById('route').value.trim()||null,
    expected_entities:j('ent',{}),
    expected_positive_constraints:j('pos',[]),
    expected_negative_constraints:j('neg',[]),
    expected_clarification_behavior:j('clar',{}),
    allowed_product_invariants:j('allow',[]),
    forbidden_product_invariants:j('forbid',[])
  };
  const res=await fetch(apiBase+'/golden/candidates/'+selected.id+'/review',{method:'POST',headers:headers(),body:JSON.stringify(body)});
  alert(await res.text()); loadCases(); if(selected) loadDetail(selected.id);
}
document.getElementById('reload').onclick=loadCases;
document.getElementById('statusFilter').onchange=loadCases;
loadCases();
</script></body></html>
"""


@router.get("/golden/review", response_class=HTMLResponse)
async def golden_review_page() -> HTMLResponse:
    return HTMLResponse(REVIEW_HTML)


@router.get("/golden/candidates")
async def list_golden_candidates(
    request: Request,
    lifecycle_status: Optional[str] = "REVIEW_REQUIRED",
    limit: int = 100,
) -> Dict[str, Any]:
    pool = _pool(request)
    limit = max(1, min(int(limit), 500))
    async with pool.acquire() as conn:
        if lifecycle_status:
            rows = await conn.fetch(
                """
                SELECT * FROM continuous_golden_cases
                WHERE lifecycle_status=$1
                ORDER BY id ASC LIMIT $2
                """,
                lifecycle_status,
                limit,
            )
        else:
            rows = await conn.fetch(
                "SELECT * FROM continuous_golden_cases ORDER BY id ASC LIMIT $1",
                limit,
            )
    return {"candidates": [_row_to_candidate(dict(r)) for r in rows], "count": len(rows)}


@router.get("/golden/candidates/{candidate_id}")
async def get_golden_candidate(candidate_id: int, request: Request) -> Dict[str, Any]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM continuous_golden_cases WHERE id=$1", candidate_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="candidate not found")
        out = _row_to_candidate(dict(row))
        samples = await conn.fetch(
            """
            SELECT s.product_id::text, s.merchant_id::text, s.category_id::text,
                   s.current_price AS price, s.currency, s.stock_status
            FROM search_ready_product_projection s
            ORDER BY random() LIMIT 5
            """
        )
        forbidden = await conn.fetch(
            """
            SELECT p.id::text AS product_id, m.merchant_code AS merchant_code
            FROM products p
            JOIN merchants m ON m.id=p.merchant_id
            WHERE NOT EXISTS (
              SELECT 1 FROM search_ready_product_projection s WHERE s.product_id=p.id
            )
            ORDER BY random() LIMIT 5
            """
        )
        out["cohort_product_samples"] = [dict(r) for r in samples]
        out["forbidden_product_samples"] = [dict(r) for r in forbidden]
        out["system_response_reference"] = {
            "note": "REFERENCE_ONLY_NOT_EXPECTED",
            "hint": "Do not copy into expected fields",
        }
        out["forbidden_finance_invariants_default"] = list(FORBIDDEN_FINANCE_INVARIANTS)
    return out


@router.get("/golden/candidates/{candidate_id}/history")
async def golden_review_history(candidate_id: int, request: Request) -> Dict[str, Any]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        exists = await conn.fetchval(
            """
            SELECT EXISTS (
              SELECT 1 FROM information_schema.tables
              WHERE table_name='continuous_golden_review_history'
            )
            """
        )
        if not exists:
            return {"history": [], "note": "history table not migrated"}
        rows = await conn.fetch(
            """
            SELECT * FROM continuous_golden_review_history
            WHERE case_pk=$1 ORDER BY created_at ASC
            """,
            candidate_id,
        )
    return {
        "history": [
            {
                **dict(r),
                "created_at": r["created_at"].isoformat() if r.get("created_at") else None,
                "payload": _parse_json(r.get("payload")),
            }
            for r in rows
        ]
    }


@router.post("/golden/candidates/{candidate_id}/claim")
async def claim_golden_candidate(
    candidate_id: int,
    payload: GoldenClaimIn,
    request: Request,
    x_taksitlio_internal_token: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin_token(x_taksitlio_internal_token, x_admin_token)
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM continuous_golden_cases WHERE id=$1", candidate_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="candidate not found")
        rv = int(row["row_version"] or 1)
        if rv != payload.row_version:
            raise HTTPException(status_code=409, detail="row_version conflict")
        if row["lifecycle_status"] not in {"REVIEW_REQUIRED", "NEEDS_REVISION", "CANDIDATE"}:
            raise HTTPException(status_code=400, detail="not claimable in current status")
        now = datetime.now(timezone.utc)
        updated = await conn.fetchrow(
            """
            UPDATE continuous_golden_cases
               SET claimed_by=$2, claimed_at=$3, row_version=row_version+1
             WHERE id=$1 AND row_version=$4
         RETURNING *
            """,
            candidate_id,
            payload.actor,
            now,
            payload.row_version,
        )
        if not updated:
            raise HTTPException(status_code=409, detail="optimistic lock failed")
        await _audit(
            conn,
            case_pk=candidate_id,
            case_id=str(row["case_id"]),
            action="CLAIM",
            actor=payload.actor,
            from_lifecycle=row["lifecycle_status"],
            to_lifecycle=row["lifecycle_status"],
            row_version_before=rv,
            row_version_after=int(updated["row_version"]),
        )
    return _row_to_candidate(dict(updated))


@router.post("/golden/candidates/{candidate_id}/prepare")
async def prepare_golden_candidate(
    candidate_id: int,
    payload: GoldenPrepareIn,
    request: Request,
    x_taksitlio_internal_token: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    """PREPARER saves expected fields. Cannot set APPROVED."""

    _require_admin_token(x_taksitlio_internal_token, x_admin_token)
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM continuous_golden_cases WHERE id=$1", candidate_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="candidate not found")
        if int(row["row_version"] or 1) != payload.row_version:
            raise HTTPException(status_code=409, detail="row_version conflict")
        if row["lifecycle_status"] == "APPROVED":
            raise HTTPException(status_code=400, detail="immutable after APPROVED")
        if not payload.review_notes.strip():
            raise HTTPException(status_code=400, detail="review_notes required")

        expected = dict(_parse_json(row.get("expected")))
        expected["expected_clarification_behavior"] = payload.expected_clarification_behavior
        expected["allowed_product_invariants"] = payload.allowed_product_invariants
        expected["forbidden_product_invariants"] = _merge_forbidden(
            payload.forbidden_product_invariants
        )
        expected["merchant_scope_codes"] = payload.merchant_scope_codes
        expected["category_scope_ids"] = payload.category_scope_ids
        expected["expected_pending_human_review"] = True
        expected["system_response_forbidden_as_expected"] = True
        constraints = {
            "positive": payload.expected_positive_constraints,
            "negative": payload.expected_negative_constraints,
        }
        now = datetime.now(timezone.utc)
        # PREPARER never approves — stay REVIEW_REQUIRED (or keep NEEDS_REVISION)
        next_life = (
            "REVIEW_REQUIRED"
            if row["lifecycle_status"] != "NEEDS_REVISION"
            else "NEEDS_REVISION"
        )
        updated = await conn.fetchrow(
            """
            UPDATE continuous_golden_cases SET
              prepared_by=$2,
              prepared_at=$3,
              expected_route=$4,
              expected_entities=$5::jsonb,
              expected_constraints=$6::jsonb,
              expected=$7::jsonb,
              review_notes=$8,
              lifecycle_status=$9,
              row_version=row_version+1,
              claimed_by=$2,
              claimed_at=COALESCE(claimed_at,$3)
             WHERE id=$1 AND row_version=$10
         RETURNING *
            """,
            candidate_id,
            payload.prepared_by,
            now,
            payload.expected_route,
            json.dumps(payload.expected_entities),
            json.dumps(constraints),
            json.dumps(expected),
            payload.review_notes.strip(),
            next_life,
            payload.row_version,
        )
        if not updated:
            raise HTTPException(status_code=409, detail="optimistic lock failed")
        await _audit(
            conn,
            case_pk=candidate_id,
            case_id=str(row["case_id"]),
            action="PREPARE",
            actor=payload.prepared_by,
            from_lifecycle=row["lifecycle_status"],
            to_lifecycle=next_life,
            row_version_before=payload.row_version,
            row_version_after=int(updated["row_version"]),
            notes=payload.review_notes.strip(),
            payload={"expected_route": payload.expected_route},
        )
    return _row_to_candidate(dict(updated))


@router.post("/golden/candidates/{candidate_id}/review")
async def review_golden_candidate(
    candidate_id: int,
    payload: GoldenReviewIn,
    request: Request,
    x_taksitlio_internal_token: Optional[str] = Header(default=None),
    x_admin_token: Optional[str] = Header(default=None),
) -> Dict[str, Any]:
    _require_admin_token(x_taksitlio_internal_token, x_admin_token)
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM continuous_golden_cases WHERE id=$1", candidate_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="candidate not found")
        if int(row["row_version"] or 1) != payload.row_version:
            raise HTTPException(status_code=409, detail="row_version conflict")
        prepared_by = row["prepared_by"]
        if not prepared_by:
            raise HTTPException(status_code=400, detail="prepared_by required before review")
        if prepared_by == payload.reviewed_by:
            raise HTTPException(
                status_code=400,
                detail="prepared_by must differ from reviewed_by (dual-control)",
            )
        if not payload.review_notes.strip():
            raise HTTPException(status_code=400, detail="review_notes required")

        decision = payload.review_decision
        forbidden = _merge_forbidden(payload.forbidden_product_invariants)
        expected = dict(_parse_json(row.get("expected")))
        expected["expected_clarification_behavior"] = (
            payload.expected_clarification_behavior
            or expected.get("expected_clarification_behavior")
            or {}
        )
        expected["allowed_product_invariants"] = (
            payload.allowed_product_invariants
            or expected.get("allowed_product_invariants")
            or []
        )
        expected["forbidden_product_invariants"] = forbidden
        expected["expected_pending_human_review"] = False
        expected["system_response_forbidden_as_expected"] = True
        constraints = {
            "positive": payload.expected_positive_constraints
            or (_parse_json(row.get("expected_constraints")).get("positive") or []),
            "negative": payload.expected_negative_constraints
            or (_parse_json(row.get("expected_constraints")).get("negative") or []),
        }
        now = datetime.now(timezone.utc)
        lifecycle = decision
        updated = await conn.fetchrow(
            """
            UPDATE continuous_golden_cases SET
              reviewed_by=$2,
              reviewed_at=$3,
              review_decision=$4,
              review_notes=$5,
              lifecycle_status=$6,
              expected_route=COALESCE($7, expected_route),
              expected_entities=$8::jsonb,
              expected_constraints=$9::jsonb,
              expected=$10::jsonb,
              review_status=CASE WHEN $4='APPROVED' THEN 'APPROVED'
                                 WHEN $4='REJECTED' THEN 'REJECTED'
                                 ELSE 'REVIEWED' END,
              row_version=row_version+1
             WHERE id=$1 AND row_version=$11
         RETURNING *
            """,
            candidate_id,
            payload.reviewed_by,
            now,
            decision,
            payload.review_notes.strip(),
            lifecycle,
            payload.expected_route,
            json.dumps(
                payload.expected_entities
                or _parse_json(row.get("expected_entities"))
                or {}
            ),
            json.dumps(constraints),
            json.dumps(expected),
            payload.row_version,
        )
        if not updated:
            raise HTTPException(status_code=409, detail="optimistic lock failed")
        action = {
            "APPROVED": "APPROVE",
            "REJECTED": "REJECT",
            "NEEDS_REVISION": "NEEDS_REVISION",
        }[decision]
        await _audit(
            conn,
            case_pk=candidate_id,
            case_id=str(row["case_id"]),
            action=action,
            actor=payload.reviewed_by,
            from_lifecycle=row["lifecycle_status"],
            to_lifecycle=lifecycle,
            row_version_before=payload.row_version,
            row_version_after=int(updated["row_version"]),
            notes=payload.review_notes.strip(),
        )
    return _row_to_candidate(dict(updated))


__all__ = ["router", "FORBIDDEN_FINANCE_INVARIANTS"]
