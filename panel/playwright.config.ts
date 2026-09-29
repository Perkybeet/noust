import { defineConfig, devices } from "@playwright/test";

// The E2E suite runs against the real backend: every worker starts its own
// scripts/console_server.py (see e2e/fixtures.ts) and uses its address as baseURL, so there
// is no web server to configure here and no port to agree on.
//
// The screenshot pass (e2e/screenshots.spec.ts, tagged @screens) is slow and produces
// files for a person to review, so it is left out of the default run. `npm run e2e:screens`
// sets NOUST_SCREENS=1, which lifts the exclusion; passing --grep alone would not, because
// Playwright applies grep and grepInvert together.
const screens = process.env.NOUST_SCREENS === "1";

// e2e/docs.spec.ts (tagged @docs) writes the pictures docs/console.md, docs/CENTRAL.md and
// README.md link, from an invented showcase server rather than the suite's own fixture
// domains. Also slow (it starts a three-server fleet), also left out of the default run, and
// also never combined with @screens: nobody runs both passes in the same invocation.
// `npm run docs:screens` sets NOUST_DOCS_SCREENS=1 and runs the light project only, since
// nothing under docs/ ships a dark-theme picture next to a light one.
const docsScreens = process.env.NOUST_DOCS_SCREENS === "1";

export default defineConfig({
  testDir: "./e2e",
  outputDir: "./test-results",
  fullyParallel: true,
  forbidOnly: Boolean(process.env.CI),
  retries: process.env.CI ? 1 : 0,
  // Each worker runs a Python backend beside its browser.
  workers: process.env.CI ? 2 : 4,
  timeout: 60_000,
  expect: { timeout: 10_000 },
  reporter: process.env.CI ? [["list"], ["html", { open: "never" }]] : "list",
  ...(screens
    ? { grep: /@screens/ }
    : docsScreens
      ? { grep: /@docs/ }
      : { grepInvert: /@screens|@docs/ }),
  use: {
    trace: "retain-on-failure",
  },
  projects: docsScreens
    ? [{ name: "light", use: { ...devices["Desktop Chrome"], colorScheme: "light" } }]
    : [
        { name: "light", use: { ...devices["Desktop Chrome"], colorScheme: "light" } },
        { name: "dark", use: { ...devices["Desktop Chrome"], colorScheme: "dark" } },
      ],
});
