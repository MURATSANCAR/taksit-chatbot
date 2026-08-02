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
});
