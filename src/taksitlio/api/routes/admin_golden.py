"""Admin golden candidate review (PREPARER ≠ REVIEWER dual-control).

System response is reference-only and must never be copied into expected.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from taksitlio.api.deps import container_from

router = APIRouter(tags=["admin-golden"])

APPROVE_REQUIRED = (
    "prepared_by",
    "reviewed_by",
    "reviewed_at",
    "review_decision",
    "review_notes",
)

FORBIDDEN_FINANCE_INVARIANTS = [
    "bank claim",
    "campaign claim",
    "monthly payment",
    "total repayment",
    "installment term",
    "zero-rate claim",
]

REVIEW_HTML = """<!DOCTYPE html>
<html lang="tr">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>Taksitlio Golden Review</title>
  <style>
    :root { --bg:#0f1419; --panel:#1a222c; --text:#e8eef4; --muted:#9aa7b5; --accent:#3d9cf0; --danger:#e85d5d; --ok:#3ecf8e; }
    * { box-sizing: border-box; }
    body { margin:0; font-family: "IBM Plex Sans", "Segoe UI", sans-serif; background:linear-gradient(160deg,#0f1419,#162032 50%,#101820); color:var(--text); }
    header { padding:1.25rem 1.5rem; border-bottom:1px solid #243040; }
    h1 { margin:0; font-size:1.35rem; letter-spacing:-0.02em; }
    .sub { color:var(--muted); font-size:0.9rem; margin-top:0.35rem; }
    main { display:grid; grid-template-columns: 320px 1fr; gap:1rem; padding:1rem 1.5rem 2rem; min-height:80vh; }
    .list, .detail { background:var(--panel); border:1px solid #243040; border-radius:10px; padding:1rem; }
    .case { padding:0.65rem 0.75rem; border-radius:8px; cursor:pointer; border:1px solid transparent; margin-bottom:0.4rem; }
    .case:hover, .case.active { border-color:var(--accent); background:#1e2a38; }
    .meta { color:var(--muted); font-size:0.8rem; }
    label { display:block; font-size:0.8rem; color:var(--muted); margin-top:0.75rem; }
    input, textarea, select { width:100%; margin-top:0.25rem; background:#101820; color:var(--text); border:1px solid #334455; border-radius:6px; padding:0.5rem; }
    textarea { min-height:72px; font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size:0.8rem; }
    .row { display:flex; gap:0.5rem; flex-wrap:wrap; margin-top:1rem; }
    button { background:var(--accent); color:#041018; border:0; border-radius:6px; padding:0.55rem 0.9rem; font-weight:600; cursor:pointer; }
    button.danger { background:var(--danger); color:#fff; }
    button.muted { background:#334455; color:var(--text); }
    .pill { display:inline-block; padding:0.15rem 0.45rem; border-radius:4px; background:#243040; font-size:0.75rem; margin-right:0.35rem; }
    .warn { color:#f0c14d; font-size:0.85rem; margin-top:0.75rem; }
    pre { white-space:pre-wrap; background:#101820; padding:0.75rem; border-radius:6px; font-size:0.78rem; max-height:220px; overflow:auto; }
    @media (max-width: 900px) { main { grid-template-columns: 1fr; } }
  </style>
</head>
<body>
  <header>
    <h1>Golden Candidate Review</h1>
    <div class="sub">PREPARER ≠ REVIEWER · system response is reference only · never auto-copy into expected</div>
  </header>
  <main>
    <section class="list">
      <label>Reviewer identity <input id="reviewer" placeholder="reviewer-id" /></label>
      <label>Status filter
        <select id="statusFilter">
          <option value="REVIEW_REQUIRED">REVIEW_REQUIRED</option>
          <option value="NEEDS_REVISION">NEEDS_REVISION</option>
          <option value="APPROVED">APPROVED</option>
          <option value="REJECTED">REJECTED</option>
          <option value="">ALL</option>
        </select>
      </label>
      <div class="row"><button type="button" id="reload">Reload</button></div>
      <div id="cases"></div>
    </section>
    <section class="detail" id="detail">
      <p class="meta">Select a candidate</p>
    </section>
  </main>
  <script>
    const apiBase = (window.TAKSITLIO_ADMIN_API_BASE || "/v1/admin");
    let selected = null;
    async function loadCases() {
      const st = document.getElementById("statusFilter").value;
      const q = st ? ("?lifecycle_status=" + encodeURIComponent(st)) : "";
      const res = await fetch(apiBase + "/golden/candidates" + q);
      const data = await res.json();
      const box = document.getElementById("cases");
      box.innerHTML = "";
      (data.candidates || []).forEach(c => {
        const el = document.createElement("div");
        el.className = "case" + (selected && selected.id === c.id ? " active" : "");
        el.innerHTML = "<div><strong>" + (c.query_text || "").slice(0, 80) + "</strong></div>" +
          "<div class='meta'><span class='pill'>" + (c.bucket || "?") + "</span>" +
          (c.lifecycle_status || "") + " · w=" + (c.demand_weight || 1) + "</div>";
        el.onclick = () => { selected = c; loadDetail(c.id); loadCases(); };
        box.appendChild(el);
      });
    }
    async function loadDetail(id) {
      const res = await fetch(apiBase + "/golden/candidates/" + id);
      const c = await res.json();
      selected = c;
      const d = document.getElementById("detail");
      d.innerHTML = `
        <div><span class="pill">${c.lifecycle_status||""}</span>
          <span class="pill">${c.bucket||""}</span>
          cohort ${c.cohort_id||"—"}/${c.cohort_version||"—"}
          · rev ${c.catalog_revision||"—"}</div>
        <h2 style="margin:0.6rem 0 0.2rem;font-size:1.1rem;">${escapeHtml(c.query_text||"")}</h2>
        <div class="meta">prepared_by=${escapeHtml(c.prepared_by||"—")} · reviewed_by=${escapeHtml(c.reviewed_by||"—")}</div>
        <p class="warn">Current system response is REFERENCE ONLY. Do not paste it into expected fields.</p>
        <label>Expected route <input id="expected_route" value="${escapeAttr(c.expected_route||"")}" /></label>
        <label>Expected entities (JSON)<textarea id="expected_entities">${escapeHtml(JSON.stringify(c.expected_entities||{},null,2))}</textarea></label>
        <label>Positive constraints (JSON)<textarea id="pos">${escapeHtml(JSON.stringify((c.expected_constraints||{}).positive||[],null,2))}</textarea></label>
        <label>Negative constraints (JSON)<textarea id="neg">${escapeHtml(JSON.stringify((c.expected_constraints||{}).negative||[],null,2))}</textarea></label>
        <label>Clarification behavior<textarea id="clar">${escapeHtml(JSON.stringify(c.expected_clarification_behavior||{},null,2))}</textarea></label>
        <label>Allowed product invariants (JSON)<textarea id="allow">${escapeHtml(JSON.stringify(c.allowed_product_invariants||[],null,2))}</textarea></label>
        <label>Forbidden product invariants (JSON)<textarea id="forbid">${escapeHtml(JSON.stringify(c.forbidden_product_invariants||[],null,2))}</textarea></label>
        <label>Review notes<textarea id="notes">${escapeHtml(c.review_notes||"")}</textarea></label>
        <h3 style="margin-top:1rem;font-size:0.95rem;">Cohort product samples</h3>
        <pre>${escapeHtml(JSON.stringify(c.cohort_product_samples||[],null,2))}</pre>
        <h3 style="margin-top:1rem;font-size:0.95rem;">Forbidden product samples</h3>
        <pre>${escapeHtml(JSON.stringify(c.forbidden_product_samples||[],null,2))}</pre>
        <h3 style="margin-top:1rem;font-size:0.95rem;">System response (reference)</h3>
        <pre>${escapeHtml(JSON.stringify(c.system_response_reference||{},null,2))}</pre>
        <div class="row">
          <button type="button" onclick="decide('APPROVED')">Approve</button>
          <button type="button" class="muted" onclick="decide('NEEDS_REVISION')">Needs revision</button>
          <button type="button" class="danger" onclick="decide('REJECTED')">Reject</button>
        </div>
      `;
    }
    function parseJson(id, fallback) {
      const raw = document.getElementById(id).value.trim();
      if (!raw) return fallback;
      return JSON.parse(raw);
    }
    async function decide(decision) {
      const reviewer = document.getElementById("reviewer").value.trim();
      if (!reviewer) { alert("Reviewer identity required"); return; }
      const body = {
        reviewed_by: reviewer,
        review_decision: decision,
        review_notes: document.getElementById("notes").value.trim(),
        expected_route: document.getElementById("expected_route").value.trim() || null,
        expected_entities: parseJson("expected_entities", {}),
        expected_positive_constraints: parseJson("pos", []),
        expected_negative_constraints: parseJson("neg", []),
        expected_clarification_behavior: parseJson("clar", {}),
        allowed_product_invariants: parseJson("allow", []),
        forbidden_product_invariants: parseJson("forbid", FORBIDDEN_DEFAULT),
      };
      const res = await fetch(apiBase + "/golden/candidates/" + selected.id + "/review", {
        method: "POST",
        headers: {"Content-Type":"application/json"},
        body: JSON.stringify(body),
      });
      const data = await res.json();
      if (!res.ok) { alert(data.detail || JSON.stringify(data)); return; }
      await loadCases();
      if (data.id) await loadDetail(data.id);
    }
    const FORBIDDEN_DEFAULT = ${json.dumps(FORBIDDEN_FINANCE_INVARIANTS)};
    function escapeHtml(s){return String(s).replace(/[&<>"']/g,c=>({"&":"&amp;","<":"&lt;",">":"&gt;","\\"":"&quot;","'":"&#39;"}[c]));}
    function escapeAttr(s){return escapeHtml(s).replace(/`/g,""); }
    document.getElementById("reload").onclick = loadCases;
    document.getElementById("statusFilter").onchange = loadCases;
    loadCases();
  </script>
</body>
</html>
"""


class GoldenReviewIn(BaseModel):
    reviewed_by: str = Field(..., min_length=1)
    review_decision: str = Field(..., pattern="^(APPROVED|REJECTED|NEEDS_REVISION)$")
    review_notes: str = Field(..., min_length=1)
    expected_route: Optional[str] = None
    expected_entities: Dict[str, Any] = Field(default_factory=dict)
    expected_positive_constraints: List[Any] = Field(default_factory=list)
    expected_negative_constraints: List[Any] = Field(default_factory=list)
    expected_clarification_behavior: Dict[str, Any] = Field(default_factory=dict)
    allowed_product_invariants: List[Any] = Field(default_factory=list)
    forbidden_product_invariants: List[Any] = Field(default_factory=list)


def _pool(request: Request):
    container = container_from(request)
    pool = container.extras.get("db_pool") or container.extras.get("pg_pool")
    if pool is None:
        raise HTTPException(status_code=501, detail="db_pool not configured")
    return pool


def _row_to_candidate(row: Any) -> dict[str, Any]:
    expected = row["expected"] or {}
    if isinstance(expected, str):
        expected = json.loads(expected)
    constraints = row["expected_constraints"] or {}
    if isinstance(constraints, str):
        constraints = json.loads(constraints)
    return {
        "id": int(row["id"]),
        "case_id": row["case_id"],
        "query_text": row["query_text"],
        "lifecycle_status": row["lifecycle_status"],
        "prepared_by": row["prepared_by"],
        "reviewed_by": row["reviewed_by"],
        "reviewed_at": row["reviewed_at"].isoformat() if row.get("reviewed_at") else None,
        "review_decision": row.get("review_decision"),
        "review_notes": row.get("review_notes"),
        "bucket": row.get("bucket") or expected.get("bucket") or row.get("source_signal"),
        "demand_weight": float(row["demand_weight"]) if row.get("demand_weight") is not None else float(expected.get("demand_weight") or 1),
        "cohort_id": row.get("cohort_id") or expected.get("cohort_id"),
        "cohort_version": row.get("cohort_version") or expected.get("cohort_version"),
        "catalog_revision": row.get("catalog_revision"),
        "source_query_id": row.get("source_query_id") or expected.get("source_query_id"),
        "expected_route": row.get("expected_route"),
        "expected_entities": row.get("expected_entities") or {},
        "expected_constraints": constraints,
        "expected_clarification_behavior": (expected or {}).get("expected_clarification_behavior") or {},
        "allowed_product_invariants": (expected or {}).get("allowed_product_invariants") or [],
        "forbidden_product_invariants": (expected or {}).get("forbidden_product_invariants")
        or list(FORBIDDEN_FINANCE_INVARIANTS),
        "expected": expected,
    }


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
                ORDER BY id ASC
                LIMIT $2
                """,
                lifecycle_status,
                limit,
            )
        else:
            rows = await conn.fetch(
                """
                SELECT * FROM continuous_golden_cases
                ORDER BY id ASC
                LIMIT $1
                """,
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
        # Cohort samples from search-ready projection (reference, not expected)
        samples = await conn.fetch(
            """
            SELECT product_id::text, merchant_id::text, category_id::text,
                   title, price, currency, primary_cdn_url
            FROM search_ready_product_projection
            ORDER BY random()
            LIMIT 5
            """
        )
        forbidden = await conn.fetch(
            """
            SELECT p.id::text AS product_id, m.code AS merchant_code
            FROM products p
            JOIN merchants m ON m.id=p.merchant_id
            WHERE NOT EXISTS (
              SELECT 1 FROM search_ready_product_projection s WHERE s.product_id=p.id
            )
            ORDER BY random()
            LIMIT 5
            """
        )
        out["cohort_product_samples"] = [dict(r) for r in samples]
        out["forbidden_product_samples"] = [dict(r) for r in forbidden]
        out["system_response_reference"] = {
            "note": "REFERENCE_ONLY_NOT_EXPECTED",
            "parser_result": None,
            "hint": "Fetch live via POST /v1/search-sessions if needed; do not copy into expected",
        }
        out["forbidden_finance_invariants_default"] = list(FORBIDDEN_FINANCE_INVARIANTS)
    return out


@router.post("/golden/candidates/{candidate_id}/review")
async def review_golden_candidate(
    candidate_id: int, payload: GoldenReviewIn, request: Request
) -> Dict[str, Any]:
    pool = _pool(request)
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            "SELECT * FROM continuous_golden_cases WHERE id=$1", candidate_id
        )
        if not row:
            raise HTTPException(status_code=404, detail="candidate not found")
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
        forbidden = list(payload.forbidden_product_invariants or [])
        for inv in FORBIDDEN_FINANCE_INVARIANTS:
            if inv not in forbidden:
                forbidden.append(inv)

        expected = row["expected"] or {}
        if isinstance(expected, str):
            expected = json.loads(expected)
        expected = dict(expected or {})
        # Never auto-copy system response; store reviewer-authored expectations only
        expected["expected_clarification_behavior"] = payload.expected_clarification_behavior
        expected["allowed_product_invariants"] = payload.allowed_product_invariants
        expected["forbidden_product_invariants"] = forbidden
        expected["expected_pending_human_review"] = False
        expected["system_response_forbidden_as_expected"] = True

        constraints = {
            "positive": payload.expected_positive_constraints,
            "negative": payload.expected_negative_constraints,
        }
        now = datetime.now(timezone.utc)
        lifecycle = decision  # APPROVED | REJECTED | NEEDS_REVISION

        await conn.execute(
            """
            UPDATE continuous_golden_cases SET
              reviewed_by=$2,
              reviewed_at=$3,
              review_decision=$4,
              review_notes=$5,
              lifecycle_status=$6,
              expected_route=$7,
              expected_entities=$8::jsonb,
              expected_constraints=$9::jsonb,
              expected=$10::jsonb,
              review_status=CASE WHEN $4='APPROVED' THEN 'APPROVED'
                                 WHEN $4='REJECTED' THEN 'REJECTED'
                                 ELSE 'REVIEWED' END
            WHERE id=$1
            """,
            candidate_id,
            payload.reviewed_by,
            now,
            decision,
            payload.review_notes.strip(),
            lifecycle,
            payload.expected_route,
            json.dumps(payload.expected_entities),
            json.dumps(constraints),
            json.dumps(expected),
        )
        updated = await conn.fetchrow(
            "SELECT * FROM continuous_golden_cases WHERE id=$1", candidate_id
        )
    return _row_to_candidate(dict(updated))


__all__ = ["router", "FORBIDDEN_FINANCE_INVARIANTS"]
