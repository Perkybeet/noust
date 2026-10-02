/**
 * The console's part of Noust 3.2 (flow U2) against the real backend, in both themes, with the
 * CSP and console gates of the `problems` fixture:
 *
 * - going back past a deployment that changed the database names it and its migrations, and
 *   the restore command, before anything is sent confirmed (the refusal is the backend's
 *   contract, answered here so the seeded release app is not rolled back under other tests);
 * - an application's deploy hooks: the operator's YAML saved behind "Confirm it's you", then
 *   removed again;
 * - the server's time zone chosen from the server's own zones, with the offset and the time
 *   it will read;
 * - adopting a running stack from "New application" shows Compose's dry run verbatim before
 *   anything is adopted (the seeded machine has no Docker: the adoption's answers are the
 *   contract's, answered here);
 * - the sites list says who wrote each site and which application it serves;
 * - whether a feature is on is a FeatureState, not a neutral notice.
 */

import type { Page, Route } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const RELEASES_APP = "tienda.example.org";
const LIVE_APP = "pedidos.example.org";

interface Row {
  id: number;
  git_commit: string | null;
  release_id: string | null;
  rollback_available: boolean;
}

async function deployments(page: Page, domain: string): Promise<Row[]> {
  const response = await page.request.get(`/api/deployments?domain=${domain}&limit=50`);
  return ((await response.json()) as { items: Row[] }).items;
}

/** The card a heading titles: its nearest section, not the subsection around every card. */
function card(page: Page, title: string) {
  return page.getByRole("heading", { name: title, exact: true }).locator("xpath=ancestor::section[1]");
}

/** Answers a GET with the real body, changed. */
async function amend(page: Page, path: string, change: (body: Record<string, unknown>) => Record<string, unknown>): Promise<void> {
  await page.route(`**${path}`, async (route: Route) => {
    if (route.request().method() !== "GET") {
      await route.continue();
      return;
    }
    const response = await route.fetch();
    await route.fulfill({ response, json: change((await response.json()) as Record<string, unknown>) });
  });
}

test("going back past a schema change names the deployment, its migration and the restore command", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 409/);
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}/deployments`);
  const rows = await deployments(page, RELEASES_APP);
  const target = rows.find((row) => row.git_commit === "19d3f6e" && row.release_id !== null && row.rollback_available);
  const later = rows.find((row) => target !== undefined && row.id > target.id);
  if (target === undefined || later === undefined) throw new Error("no seeded deployment to go back to");

  await amend(page, `/api/deployments/${String(target.id)}`, (body) => ({ ...body, schema_changed_between: [later.id] }));
  await amend(page, `/api/deployments/${String(later.id)}`, (body) => ({
    ...body,
    schema_changed: true,
    hooks: [
      {
        phase: "pre_deploy",
        run: "npx prisma migrate deploy",
        service: null,
        migrates: true,
        ok: true,
        exit_code: 0,
        timed_out: false,
        duration_s: 3,
        output: "All migrations have been successfully applied.",
        automatic: null,
      },
    ],
  }));
  const sent: unknown[] = [];
  await page.route(`**/api/apps/${RELEASES_APP}/deployments/${String(target.id)}/rollback`, async (route) => {
    sent.push(route.request().postDataJSON());
    await route.fulfill({
      status: 409,
      json: {
        error: "schema_changed",
        detail: `Going back to deployment ${String(target.id)} of ${RELEASES_APP} passes deployment ${String(later.id)}, which changed the database schema`,
        hint: "Noust puts the code back, never the database.",
        fields: null,
        output: null,
        deployments: [later.id],
      },
    });
  });

  await page.goto(`/apps/${RELEASES_APP}/deployments/${String(target.id)}`);
  await page.getByRole("button", { name: "Go back to this version" }).click();
  const first = page.getByRole("dialog", { name: `Go back to deployment ${String(target.id)}?` });
  await expect(first.getByText(`Deployment #${String(later.id)} changed the database schema after this one.`, { exact: false })).toBeVisible();
  await first.getByRole("button", { name: "Go back", exact: true }).click();

  const dialog = page.getByRole("dialog", { name: "Going back passes a change to the database" });
  await expect(dialog.getByRole("link", { name: `Deployment ${String(later.id)}` })).toBeVisible();
  await expect(dialog.getByText("npx prisma migrate deploy")).toBeVisible();
  await expect(dialog.getByText(/noust backup (list|restore)/).first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the schema change confirmation");
  expect(sent).toEqual([{ schema_changed_ok: false }]);
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("an application's deploy hooks: the operator's YAML saved behind sudo mode, then removed", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${LIVE_APP}/settings/hooks`);
  await expect(page.getByRole("heading", { level: 2, name: "Deploy hooks" })).toBeVisible();
  const inUse = card(page, "In use");
  await expect(inUse.getByText("None", { exact: true })).toBeVisible();

  await page.getByRole("textbox", { name: "Hooks (YAML)" }).fill("hooks:\n  pre_deploy:\n    - run: npx prisma migrate deploy\n      migrates: true\n");
  await page.getByRole("region", { name: "Unsaved changes" }).getByRole("button", { name: "Save" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(inUse.getByText("From the operator")).toBeVisible();
  await expect(inUse.getByRole("list", { name: "Before serving" }).getByText("npx prisma migrate deploy")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the deploy hooks");

  // Back as it was, for the next test on this worker's machine.
  await page.getByRole("button", { name: "Remove the operator's hooks" }).click();
  const remove = page.getByRole("alertdialog", { name: `Remove the operator's hooks of ${LIVE_APP}?` });
  await remove.getByRole("button", { name: "Remove the operator's hooks" }).click();
  await expect(inUse.getByText("None", { exact: true })).toBeVisible();
});

test("the server's time zone is chosen from its own zones, with the offset and the time it will read", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/system");
  await page.getByRole("button", { name: "Change time zone" }).click();
  const dialog = page.getByRole("dialog", { name: "Change the time zone" });
  const zone = dialog.getByRole("combobox", { name: "Time zone" });
  await zone.click();
  await zone.fill("madrid");
  const option = page.getByRole("option", { name: /Europe\/Madrid/ });
  await expect(option).toContainText(/UTC\+0[12]:00 · CES?T/);
  await option.click();
  await expect(dialog.getByRole("status")).toContainText(/With this zone the server runs on UTC\+0[12]:00/);
  await expect(dialog.getByText(/^Its clock would read .* now\.$/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the time zone dialog");
  await dialog.getByRole("button", { name: "Cancel" }).click();
});

test("adopting a running stack previews first, with Compose's dry run verbatim", async ({ page, consoleServer }) => {
  const dryRun = " Container shop-db-1  Running\n Container shop-web-1  Running";
  const bodies: unknown[] = [];
  await page.route("**/api/apps/adopt", async (route) => {
    bodies.push(route.request().postDataJSON());
    await route.fulfill({
      json: {
        domain: "stack.example.org",
        app_name: "stack-example-org",
        app_path: "/srv/stack",
        compose_file: "docker-compose.yml",
        project: "shop",
        project_from: "containers",
        containers: ["shop-db-1", "shop-web-1"],
        running: true,
        source: "https://github.com/acme/stack.git",
        branch: "main",
        commit: "4be2c1d",
        site: "/etc/nginx/sites-available/stack",
        site_name: "stack",
        ssl: true,
        port: 8080,
        headless: false,
        dry_run: dryRun,
        changes: [],
        warnings: [],
        adopted: false,
        unit: "stack-example-org",
        deployment_id: null,
      },
    });
  });
  await signIn(page, consoleServer, "/apps/new");
  await page.getByRole("radio", { name: "A running stack" }).click();
  await page.getByRole("textbox", { name: "Domain" }).fill("stack.example.org");
  await page.getByRole("textbox", { name: "Directory" }).fill("/srv/stack");
  await page.getByRole("button", { name: "Preview" }).click();
  await expect(page.getByRole("heading", { name: "What Noust found" })).toBeVisible();
  await expect(page.locator("pre").filter({ hasText: "Container shop-web-1  Running" })).toHaveText(dryRun);
  await expect(page.getByRole("button", { name: "Adopt" })).toBeVisible();
  expect(bodies).toEqual([{ domain: "stack.example.org", path: "/srv/stack", preview: true, accept_recreate: false }]);
  await settle(page);
  await expectNoA11yViolations(page, "the adoption's preview");
});

test("the sites list says who wrote each site and which application it serves", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/domains/sites");
  await expect(page.getByRole("columnheader", { name: "Written by" })).toBeVisible();
  await expect(page.getByRole("columnheader", { name: "Application" })).toBeVisible();
  await expect(page.getByRole("cell", { name: "Noust" }).first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the sites list");
});

test("whether a feature is on reads at a glance: instant rollback and zero-downtime deploys", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${RELEASES_APP}/settings/deploys`);
  const rollback = card(page, "Instant rollback");
  await expect(rollback.locator('[data-feature-state][data-state="on"]')).toBeVisible();
  const zeroDowntime = card(page, "Zero-downtime deploys");
  await expect(zeroDowntime.locator("[data-feature-state]")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the deploy settings");
});
