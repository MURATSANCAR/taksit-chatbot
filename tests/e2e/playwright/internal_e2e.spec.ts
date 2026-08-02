/**
 * P3.5 INTERNAL Playwright suite scaffold — real API only, no mocks.
 * Requires: PLAYWRIGHT + TAKSITLIO_API_BASE + TAKSITLIO_INTERNAL_TOKEN
 */
import { test, expect } from "@playwright/test";

const API = process.env.TAKSITLIO_API_BASE || "http://127.0.0.1:8040";
const TOKEN = process.env.TAKSITLIO_INTERNAL_TOKEN || "";
const COHORT_ID = process.env.TAKSITLIO_COHORT_ID || "1";
const COHORT_VER = process.env.TAKSITLIO_COHORT_VERSION || "1";

function internalHeaders() {
  return {
    "Content-Type": "application/json",
    "X-Taksitlio-Traffic": "internal",
    "X-Taksitlio-Internal-Token": TOKEN,
    "X-Taksitlio-Cohort-Id": COHORT_ID,
    "X-Taksitlio-Cohort-Version": COHORT_VER,
  };
}

test.describe("INTERNAL API E2E", () => {
  test("1. INTERNAL access success", async ({ request }) => {
    const res = await request.post(`${API}/v1/search-sessions`, {
      headers: internalHeaders(),
      data: { conversation_id: "pw-1", message: "samsung telefon" },
    });
    expect(res.status()).toBeLessThan(400);
    const body = await res.json();
    expect(body.search_session_id).toBeTruthy();
  });

  test("2. Unauthorized INTERNAL access denied", async ({ request }) => {
    const res = await request.post(`${API}/v1/search-sessions`, {
      headers: {
        "Content-Type": "application/json",
        "X-Taksitlio-Traffic": "internal",
        "X-Taksitlio-Internal-Token": "forged-bad-token",
        "X-Taksitlio-Cohort-Id": COHORT_ID,
      },
      data: { conversation_id: "pw-2", message: "samsung telefon" },
    });
    expect(res.status()).toBe(403);
  });

  test("3. Fast-path product search", async ({ request }) => {
    const res = await request.post(`${API}/v1/search-sessions`, {
      headers: internalHeaders(),
      data: { conversation_id: "pw-3", message: "laptop" },
    });
    expect(res.ok()).toBeTruthy();
  });

  test("4. Complex multi-constraint returns plan or products", async ({ request }) => {
    const res = await request.post(`${API}/v1/search-sessions`, {
      headers: internalHeaders(),
      data: {
        conversation_id: "pw-4",
        message:
          "40 bin TL laptop, 16 GB RAM şart, HP olmasın Lenovo tercih, çok iyiyse 45 bine çıkabilirim",
      },
    });
    expect(res.status()).toBeLessThan(400);
    const body = await res.json();
    expect(body.search_session_id).toBeTruthy();
    // Plan may be attached on FAST completions; clarification is also valid.
    if (body.canonical_plan) {
      expect(body.canonical_plan.plan_version || body.canonical_plan.request_type).toBeTruthy();
    }
  });

  test("5. Finance unavailable firewall — no invented payments", async ({ request }) => {
    const res = await request.post(`${API}/v1/search-sessions`, {
      headers: internalHeaders(),
      data: { conversation_id: "pw-5", message: "12 ay taksitli en düşük aylık ödemeli laptop" },
    });
    expect(res.status()).toBeLessThan(400);
    const body = await res.json();
    const products =
      body?.partial_results?.products || body?.results?.products || body?.products || [];
    for (const p of products) {
      expect(p.best_finance_summary == null || p.best_finance_summary === undefined).toBeTruthy();
      expect(p.monthly_payment == null || p.monthly_payment === undefined).toBeTruthy();
    }
  });

  test("6. Constraint RELAX action accepted", async ({ request }) => {
    const create = await request.post(`${API}/v1/search-sessions`, {
      headers: internalHeaders(),
      data: { conversation_id: "pw-6", message: "16 GB RAM şart laptop" },
    });
    expect(create.status()).toBeLessThan(400);
    const body = await create.json();
    const sid = body.search_session_id;
    const qv = body.query_version || 1;
    if (!sid) return;
    const relax = await request.post(`${API}/v1/search-sessions/${sid}/constraints`, {
      headers: internalHeaders(),
      data: {
        action: "RELAX",
        constraint_id: "ram",
        value: null,
        expected_query_version: qv,
      },
    });
    // 200 retrieve or 4xx validation — must not 5xx
    expect(relax.status()).toBeLessThan(500);
  });
});
