/**
 * The new-app wizard's "From GitHub" source against the real backend: the seeded machine has
 * its own GitHub App, so the wizard opens on the repositories the App's installations reach
 * (answered by scripts/console_server.py's fake GitHub, offline). Choosing a repository and a
 * branch carries the installation that reads it into the inspection and into the deploy
 * request, both checked here as the browser sent them; the clone the inspection runs checks
 * out the seeded storefront project, so the review proposes what a real repository would.
 */

import { forgetApp } from "./apps-cleanup";
import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, stillness, test } from "./fixtures";

test("a repository and branch chosen from GitHub reach the inspection and the deploy with their installation", async ({ page, consoleServer, problems }) => {
  test.setTimeout(240_000);
  // Reading a source as root is sudo mode: the first inspection of a session is refused until
  // "Confirm it's you", and retried.
  problems.expect(/status of 403 .* \/api\/apps\/inspect$/);
  const domain = "desde-github.example.net";
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
  await expect(repositories.first()).toContainText("acme/landing");
  await search.press("Enter");
  const change = page.getByRole("button", { name: "Change repository" });
  await expect(change).toBeFocused();
  await expect(page.getByText("acme/landing", { exact: true })).toBeVisible();

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
    source: "github:acme/landing",
    branch: "develop",
    github_installation_id: 61000001,
  });
  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  const review = page.getByRole("heading", { level: 2, name: "Review" });
  await expect(confirm.or(review)).toBeVisible();
  if (await confirm.isVisible()) await confirmItsYou(page, consoleServer);
  await expect(review).toBeFocused();

  const found = page.getByRole("region", { name: "What Noust found" });
  await expect(found.getByText(/^Noust can deploy this as Next\.js/)).toBeVisible();
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
    source: "github:acme/landing",
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
