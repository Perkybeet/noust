/**
 * The new-app wizard's "From GitHub" source against the real backend: the seeded machine has
 * its own GitHub App, so the wizard opens on the repositories the App's installations reach
 * (answered by scripts/console_server.py's fake GitHub, offline). Choosing a repository and a
 * branch carries the installation that reads it into the inspection and into the deploy
 * request, both checked here as the browser sent them; the clone the inspection runs checks
 * out the seeded storefront project, so the review proposes what a real repository would.
 */

import type { Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, stillness, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

/** The CSRF header every write through `page.request` carries, mirrored from its cookie. */
async function csrf(page: Page): Promise<Record<string, string>> {
  const cookie = (await page.context().cookies()).find((entry) => entry.name === "wasm_csrf");
  return cookie ? { "X-WASM-CSRF": cookie.value } : {};
}

/**
 * Leaves the worker's machine as seeded once the deploy has ended: other specs count its apps.
 * A first deploy that failed has removed its app already; one that succeeded is deleted.
 */
async function forgetApp(page: Page, server: ConsoleServer, domain: string): Promise<void> {
  await page.goto("about:blank");
  await expect
    .poll(
      async () => {
        const active = (await (await page.request.get("/api/jobs/active")).json()) as { jobs: { metadata?: { domain?: string } }[] };
        return active.jobs.some((job) => job.metadata?.domain === domain);
      },
      { timeout: 120_000, intervals: [1_000] },
    )
    .toBe(false);
  if ((await page.request.get(`/api/apps/${domain}`)).status() === 404) return;
  const elevated = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(elevated.ok(), await elevated.text()).toBe(true);
  const deleted = await page.request.delete(`/api/apps/${domain}?remove_files=true&remove_ssl=true`, { headers: await csrf(page) });
  expect(deleted.ok(), await deleted.text()).toBe(true);
  await expect.poll(async () => (await page.request.get(`/api/apps/${domain}`)).status(), { timeout: 60_000 }).toBe(404);
}

test("a repository and branch chosen from GitHub reach the inspection and the deploy with their installation", async ({ page, consoleServer, problems }) => {
  test.setTimeout(240_000);
  // Reading a source as root is sudo mode: the first inspection of a session is refused until
  // "Confirm it's you", and retried.
  problems.expect(/status of 403 .* \/api\/apps\/inspect$/);
  const domain = "desde-github.qrboda.com";
  await signIn(page, consoleServer, "/apps/new");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("New application");

  const where = page.getByRole("radiogroup", { name: "Where the code is" });
  await expect(where.getByRole("radio", { name: "From GitHub" })).toBeChecked();
  const search = page.getByRole("combobox", { name: "Repository" });
  const repositories = page.getByRole("listbox", { name: "Repositories" }).getByRole("option");
  await expect(repositories).toHaveCount(5);
  await expect(page.getByText("5 repositories the App can read.", { exact: false })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the GitHub repository picker");

  // Typed to filter, chosen from the keyboard.
  await search.fill("land");
  await expect(repositories).toHaveCount(1);
  await expect(repositories.first()).toContainText("arennalabs/landing");
  await search.press("Enter");
  const change = page.getByRole("button", { name: "Change repository" });
  await expect(change).toBeFocused();
  await expect(page.getByText("arennalabs/landing", { exact: true })).toBeVisible();

  // The default branch is chosen; another is picked from GitHub's list.
  const branch = page.getByRole("combobox", { name: "Branch" });
  await expect(branch).toHaveText(/main/);
  await branch.click();
  await expect(page.getByRole("option", { name: /main/ })).toContainText("Default branch");
  await page.getByRole("option", { name: /^develop/ }).click();
  await expect(branch).toHaveText(/develop/);
  await settle(page);
  await expectNoA11yViolations(page, "a repository and branch chosen");

  const inspected = page.waitForRequest(
    (request) => request.url().endsWith("/api/apps/inspect") && request.method() === "POST",
  );
  await page.getByRole("button", { name: "Inspect source" }).click();
  expect((await inspected).postDataJSON()).toMatchObject({
    source: "github:arennalabs/landing",
    branch: "develop",
    github_installation_id: 61000001,
  });
  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  const review = page.getByRole("heading", { level: 2, name: "Review" });
  await expect(confirm.or(review)).toBeVisible();
  if (await confirm.isVisible()) await confirmItsYou(page, consoleServer);
  await expect(review).toBeFocused();

  const found = page.getByRole("region", { name: "What WASM found" });
  await expect(found.getByText(/^WASM can deploy this as Next\.js/)).toBeVisible();
  await page.getByLabel("Domain", { exact: true }).fill(domain);
  await expect(page.getByText(`${domain} points here`)).toBeVisible();
  await page.getByLabel(/^DATABASE_URL/).fill("postgres://landing@localhost/landing");
  for (const name of ["NEXTAUTH_SECRET", "STRIPE_SECRET_KEY", "SMTP_PASSWORD"]) {
    await page.getByRole("button", { name: `Generate ${name}` }).click();
  }
  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Deploy" })).toBeFocused();
  await stillness(page);
  await expectNoA11yViolations(page, "the deploy step of a GitHub source");

  const queued = page.waitForRequest((request) => request.url().endsWith("/api/apps") && request.method() === "POST");
  await page.getByRole("button", { name: `Deploy ${domain}` }).click();
  expect((await queued).postDataJSON()).toMatchObject({
    domain,
    source: "github:arennalabs/landing",
    branch: "develop",
    github_installation_id: 61000001,
    app_type: "nextjs",
  });
  await expect(page).toHaveURL(new RegExp(`/apps/${domain.replace(/\./g, "\\.")}/deployments/\\d+$`), { timeout: 30_000 });

  await forgetApp(page, consoleServer, domain);
});

test("the typed source is one choice away; each choice starts its source afresh", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps/new");
  const where = page.getByRole("radiogroup", { name: "Where the code is" });
  await expect(where.getByRole("radio", { name: "From GitHub" })).toBeChecked();
  await page.getByRole("listbox", { name: "Repositories" }).getByRole("option", { name: /yago-lopez\/portfolio/ }).click();
  await expect(page.getByText("yago-lopez/portfolio", { exact: true })).toBeVisible();

  await where.getByRole("radio", { name: "URL or path" }).click();
  await expect(page.getByLabel("Repository or directory")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the typed source beside a connected GitHub App");

  // Another source altogether: the repository chosen before is not carried over.
  await where.getByRole("radio", { name: "From GitHub" }).click();
  const options = page.getByRole("listbox", { name: "Repositories" }).getByRole("option");
  await expect(options).toHaveCount(5);
  await options.filter({ hasText: "yago-lopez/portfolio" }).click();
  await page.getByRole("button", { name: "Change repository" }).click();
  await expect(page.getByRole("combobox", { name: "Repository" })).toBeFocused();
  await page.getByRole("button", { name: "Keep the chosen repository" }).click();
  await expect(page.getByRole("button", { name: "Change repository" })).toBeVisible();
  await expect(page.getByText("yago-lopez/portfolio", { exact: true })).toBeVisible();
});
