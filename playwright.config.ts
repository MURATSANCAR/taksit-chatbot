import { defineConfig, devices } from "@playwright/test";

/**
 * Config from env only — no hardcoded host/token/product IDs.
 * TAKSITLIO_API_BASE, TAKSITLIO_PORTAL_BASE, TAKSITLIO_INTERNAL_TOKEN,
 * TAKSITLIO_COHORT_ID, TAKSITLIO_COHORT_VERSION
 */
export default defineConfig({
  testDir: "./tests/e2e/playwright",
  timeout: 60_000,
  fullyParallel: false,
  forbidOnly: !!process.env.CI,
  retries: 0,
  reporter: [["list"], ["json", { outputFile: "artifacts/e2e-production-verification/p3-7-product-search-internal-go/playwright-report.json" }]],
  use: {
    baseURL: process.env.TAKSITLIO_PORTAL_BASE || process.env.TAKSITLIO_API_BASE,
    trace: "on-first-retry",
    screenshot: "only-on-failure",
    video: "retain-on-failure",
  },
  projects: [
    { name: "chromium", use: { ...devices["Desktop Chrome"] } },
  ],
});
