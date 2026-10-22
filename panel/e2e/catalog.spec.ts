/**
 * The catalog of recipes against the real backend, in both themes (checklist D-03, D-15, D-16):
 * a card per recipe with a logo the strict policy lets through, search and the category, tag and
 * kind filters, paging at the operator's size, a recipe's own page, the install of a recipe from
 * the catalog to its end as a job whose output is a click away, and the Installed tab with its
 * update. The click budget of the recipe path is click-budget.spec.ts's.
 */

import type { Locator, Page } from "@playwright/test";

import { csrf, forgetApp } from "./apps-cleanup";
import { spanish } from "./catalog";
import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";
import { expectNoEnglishLeftovers, signInSpanish, useSpanish } from "./spanish";

/** The recipes' list, and one card of it by the recipe's name. */
function recipes(page: Page): Locator {
  return page.getByRole("list", { name: "Recipes" });
}

function card(page: Page, title: string): Locator {
  return recipes(page).getByRole("listitem").filter({ has: page.getByRole("heading", { name: title, exact: true }) });
}

/**
 * One recipe's card, found by name as an operator does: the catalog holds seventy recipes in
 * pages of twenty, so one far down the alphabet is not on the first page.
 */
async function find(page: Page, title: string): Promise<Locator> {
  await page.getByRole("searchbox", { name: "Search recipes" }).fill(title);
  const found = card(page, title);
  await expect(found).toBeVisible();
  return found;
}

/** The names of the recipes on screen, in the order they are drawn. */
async function titles(page: Page): Promise<string[]> {
  return recipes(page).getByRole("heading").allTextContents();
}

test("the catalog draws a card for each recipe, its logo loaded under the strict policy", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/catalog");
  await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
  await expect(recipes(page).getByRole("heading")).toHaveCount(20);
  await settle(page);
  await expectNoA11yViolations(page, "the catalog");
  const vault = await find(page, "Vaultwarden");
  // The logo is the one the server serves, as an image: the page's policy lets it through (the
  // fixture fails the test on any violation), and it decoded.
  await expect.poll(() => vault.locator("img").evaluate((image: HTMLImageElement) => image.complete && image.naturalWidth > 0)).toBe(true);
  // A tile is the logo, the name, one line and Use: the rest is on the recipe's page.
  await expect(vault.getByRole("link", { name: "Vaultwarden" })).toHaveAttribute("href", "/catalog/vaultwarden");
  await expect(vault.getByRole("button", { name: "Use Vaultwarden" })).toBeEnabled();
  await expect(vault.getByText("Runs in Docker")).toHaveCount(0);
  await expect(vault.getByRole("list", { name: "Tags" })).toHaveCount(0);
});

test("the catalog is searched and filtered by the server's own search, and says when nothing matches", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/catalog");
  await expect(recipes(page).getByRole("heading")).toHaveCount(20);

  await page.getByRole("searchbox", { name: "Search recipes" }).fill("bitwarden");
  await expect(page).toHaveURL(/[?&]q=bitwarden/);
  await expect(card(page, "Vaultwarden")).toBeVisible();
  await expect(card(page, "WordPress")).toHaveCount(0);
  await expect(page.getByRole("status").filter({ hasText: /^\d+ of \d+ recipes$/ })).toBeVisible();
  await page.getByRole("button", { name: "Clear filters" }).click();
  await expect(recipes(page).getByRole("heading")).toHaveCount(20);
  await expect(page).not.toHaveURL(/q=/);

  // A category of the closed list, with how many recipes it holds.
  await page.getByRole("combobox", { name: "Category" }).click();
  await page.getByRole("option", { name: /^Security \(\d+\)$/ }).click();
  await expect(page).toHaveURL(/[?&]category=security/);
  await expect(card(page, "Vaultwarden")).toBeVisible();
  await expect(card(page, "WordPress")).toHaveCount(0);
  await page.getByRole("button", { name: "Clear filters" }).click();

  // A tag, found by typing.
  await page.getByRole("combobox", { name: "Tag" }).click();
  await page.getByRole("combobox", { name: "Tag" }).fill("bitwarden");
  await page.getByRole("option", { name: /^bitwarden \(\d+\)$/ }).click();
  await expect(page).toHaveURL(/[?&]tag=bitwarden/);
  await expect(card(page, "Vaultwarden")).toBeVisible();
  await expect(card(page, "WordPress")).toHaveCount(0);
  await page.getByRole("button", { name: "Clear filters" }).click();

  // What it runs on.
  await page.getByRole("combobox", { name: "Runs" }).click();
  await page.getByRole("option", { name: "Natively", exact: true }).click();
  await expect(page).toHaveURL(/[?&]kind=native/);
  await expect(card(page, "WordPress")).toBeVisible();
  await expect(card(page, "Vaultwarden")).toHaveCount(0);
  await page.getByRole("button", { name: "Clear filters" }).click();

  await page.getByRole("searchbox", { name: "Search recipes" }).fill("zzzz-nothing");
  await expect(page.getByText("No recipe matches these filters.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a catalog search that matches nothing");
});

test("the catalog pages at the operator's size and remembers it, with the recipes it cannot deploy listed with their reason", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/catalog");
  await expect(recipes(page).getByRole("heading")).toHaveCount(20);
  const paging = page.getByRole("group", { name: "Pagination" });
  const range = await paging.getByText(/^Showing 1–20 of \d+$/).textContent();
  const total = Number(/of (\d+)$/.exec(range ?? "")?.[1]);
  expect(total).toBeGreaterThan(40);

  const next = paging.getByRole("button", { name: "Next page" });
  await next.click();
  await expect(paging.getByText(/^Showing 21–\d+ of \d+$/)).toBeVisible();
  // The deployable recipes come first: the ones this release cannot run are on the last page
  // (or the last two), listed with the server's reason and no way to use them.
  for (let shown = 2; shown < Math.ceil(total / 20); shown++) {
    await next.click();
    await expect(paging.getByText(new RegExp(`^Showing ${String(shown * 20 + 1)}–\\d+ of \\d+$`))).toBeVisible();
  }
  const unavailable = recipes(page).getByRole("listitem").filter({ hasText: "Not available in this release" }).first();
  await expect(unavailable).toBeVisible();
  await expect(unavailable.getByRole("button")).toHaveCount(0);
  await expect(unavailable.locator("p").last()).not.toBeEmpty();

  await paging.getByRole("combobox", { name: "Rows per page" }).click();
  await page.getByRole("option", { name: "All" }).click();
  await expect.poll(async () => (await titles(page)).length).toBeGreaterThan(20);
  await settle(page);
  await expectNoA11yViolations(page, "the whole catalog");
  // Kept for this list: the page, opened again, shows all.
  await page.reload();
  await expect.poll(async () => (await titles(page)).length).toBeGreaterThan(20);
  await page.evaluate(() => {
    window.localStorage.removeItem("noust.pageSize.recipes");
  });
});

test("a recipe's own page says what it runs, needs, asks for and tells, and shows no secret", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/catalog");
  await (await find(page, "Vaultwarden")).getByRole("link", { name: "Vaultwarden" }).click();
  await expect(page).toHaveURL(/\/catalog\/vaultwarden$/);
  await expect(page.getByRole("heading", { level: 1, name: "Vaultwarden" })).toBeVisible();
  await expect(page.getByRole("navigation", { name: "Breadcrumb" }).getByRole("link", { name: "Catalog" })).toBeVisible();

  const runs = page.getByRole("region", { name: "What it runs" });
  await expect(runs.getByText("In Docker, as a Compose stack Noust writes")).toBeVisible();
  await expect(runs.getByText(/^ghcr\.io\/dani-garcia\/vaultwarden:1\.37\.4$/)).toBeVisible();
  await expect(runs.getByText(/^sha256:[a-f0-9]{16}/)).toBeVisible();

  const variables = page.getByRole("region", { name: "Variables" });
  const token = variables.getByRole("row", { name: /ADMIN_TOKEN/ });
  await expect(token.getByText("Generated for you")).toBeVisible();
  await expect(token.getByText("Secret")).toBeVisible();
  await expect(page.getByRole("region", { name: "After you install it" })).toContainText("https://<domain>");
  await settle(page);
  await expectNoA11yViolations(page, "a recipe's page");

  await page.getByRole("button", { name: "Use Vaultwarden" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Address and variables" })).toBeVisible();
  await expect(page).toHaveURL(/\/apps\/new\?.*recipe=vaultwarden/);
});

test("a recipe's ports opened to the network are said before it is installed", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/catalog/syncthing");
  const ports = page.getByRole("region", { name: "Open to the network" });
  await expect(ports.getByText("22000/tcp")).toBeVisible();
  await expect(ports.getByText("22000/udp")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a recipe that opens ports");
});

test("a recipe cannot be installed before what it cannot run without is given, and the way to it is a click", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/catalog");
  await (await find(page, "FreshRSS")).getByRole("button", { name: "Use FreshRSS" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Address and variables" })).toBeVisible();
  await expect(page.getByText("v1.30.1")).toBeVisible();
  const posted: string[] = [];
  page.on("request", (request) => {
    if (request.method() === "POST" && request.url().endsWith("/api/apps")) posted.push(request.url());
  });
  await page.getByLabel("Domain", { exact: true }).fill("lector-catalogo.example.net");
  await page.getByRole("button", { name: "Deploy", exact: true }).click();
  // Blocked, and said in a sentence that leads to the field.
  await expect(page.getByText(/needs a value before it can deploy/)).toBeVisible();
  expect(posted).toEqual([]);
  await page.getByRole("button", { name: "Go to ADMIN_EMAIL" }).click();
  await expect(page.getByLabel(/^ADMIN_EMAIL/)).toBeFocused();
  await settle(page);
  await expectNoA11yViolations(page, "a recipe waiting for a variable");
});

test("a recipe is installed from the catalog as a job with its output a click away, and then listed as installed", async ({ page, consoleServer }) => {
  test.setTimeout(300_000);
  const domain = "wp-catalogo.example.net";
  await signIn(page, consoleServer, "/catalog");
  await (await find(page, "WordPress")).getByRole("button", { name: "Use WordPress" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Address and variables" })).toBeVisible();
  await page.getByLabel("Domain", { exact: true }).fill(domain);
  await page.getByRole("button", { name: "Deploy", exact: true }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Deploy" })).toBeVisible();

  // Its output, as the job prints it, in a drawer.
  await page.getByRole("button", { name: "View output" }).click();
  const output = page.getByRole("dialog", { name: `Deploying ${domain}` });
  await expect(output).toBeVisible();
  await expect(output.getByRole("log").or(output.getByRole("region", { name: /Output of/ }))).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a recipe's job output");
  await page.keyboard.press("Escape");
  await expect(output).toBeHidden();

  await expect(page.getByText(`${domain} is deployed`)).toBeVisible({ timeout: 180_000 });
  // What the install recorded: which recipe, at which version, against the catalog's.
  await page.goto("/catalog/installed");
  const row = page.getByRole("row", { name: new RegExp(domain.replace(/\./g, "\\.")) });
  await expect(row).toBeVisible();
  await expect(row.getByText("Up to date")).toBeVisible();
  await expect(row.getByText("WordPress")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the installed recipes");
  await forgetApp(page, consoleServer, domain);
});

const withCatalogApps = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--totp", "--backup-codes", "500"], { env: { NOUST_E2E_CATALOG: "1" } });
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
});

withCatalogApps("the installed tab says which recipe is behind the catalog, which is not, and which has gone", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/catalog/installed");
  await expect(page.getByRole("heading", { level: 1, name: "Applications" })).toBeVisible();
  const table = page.getByRole("table", { name: "Applications installed from a recipe" });
  const behind = table.getByRole("row", { name: /vault\.example\.net/ });
  await expect(behind.getByText("Update available")).toBeVisible();
  await expect(behind.getByText("v1.36.0")).toBeVisible();
  await expect(behind.getByText("v1.37.4")).toBeVisible();
  const current = table.getByRole("row", { name: /reader\.example\.net/ });
  await expect(current.getByText("Up to date")).toBeVisible();
  await expect(current.getByRole("button")).toHaveCount(0);
  const gone = table.getByRole("row", { name: /retired\.example\.net/ });
  await expect(gone.getByText("Cannot be updated")).toBeVisible();
  await expect(gone.getByText("The recipe is no longer in the catalogue")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the installed recipes, one behind");
});

withCatalogApps("an update queues the one update as a job and shows its failure in the server's words", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/catalog/installed");
  await page.getByRole("button", { name: "Update vault.example.net" }).click();
  const dialog = page.getByRole("dialog", { name: "Update vault.example.net" });
  await expect(dialog.getByText("From v1.36.0 to v1.37.4, the version the catalog has now.")).toBeVisible();
  await expect(dialog.getByText("It goes back when it does not start")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the update dialog");
  const queued = page.waitForRequest((request) => request.url().endsWith("/api/recipes/installed/vault.example.net/update") && request.method() === "POST");
  await dialog.getByRole("button", { name: "Update" }).click();
  expect(((await queued).postDataJSON() as { variables: object; force: boolean })).toEqual({ variables: {}, force: false });
  await expect(dialog).toBeHidden();
  // The machine the console server models has no Compose file for it: the job fails and says so,
  // as the server wrote it, where the update was started.
  await expect(page.getByText("vault.example.net was not updated")).toBeVisible({ timeout: 60_000 });
  await expect(page.getByRole("main").getByText(/Could not find the compose file of vault\.example\.net/)).toBeVisible();
  await expect(page.getByRole("button", { name: "View output" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a failed recipe update");
});

withCatalogApps("a container application's settings show how its image is made and never a secret", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps/crate.example.net/settings/container");
  await expect(page.getByRole("heading", { level: 2, name: "Container" })).toBeVisible();
  const main = page.getByRole("main");
  await expect(main.getByLabel(/^Dockerfile/)).toHaveValue("docker/Dockerfile.prod");
  await expect(main.getByLabel(/^Build context/)).toHaveValue("services/api");
  await expect(main.getByLabel(/^Build stage/)).toHaveValue("runtime");
  await expect(main.getByLabel(/^Container port/)).toHaveValue("3000");
  // A build secret the server keeps: its id, that it is kept, and nothing of its value anywhere.
  await expect(main.getByText("npmrc", { exact: true })).toBeVisible();
  await expect(main.getByText("Kept on the server", { exact: true })).toBeVisible();
  expect(await page.content()).not.toContain("seeded-never-shown");
  await settle(page);
  await expectNoA11yViolations(page, "a container application's settings");

  // The port and a volume, saved in one request that names only what changed.
  await main.getByLabel(/^Container port/).fill("3100");
  await main.getByRole("button", { name: "Add volume" }).click();
  await main.getByLabel(/^Path in the container/).last().fill("/uploads");
  const saved = page.waitForRequest((request) => request.url().endsWith("/api/apps/crate.example.net/container") && request.method() === "PUT");
  await page.getByRole("region", { name: "Unsaved changes" }).getByRole("button", { name: "Save" }).click();
  expect((await saved).postDataJSON()).toEqual({ clear_registry: false, container_port: 3100, volumes: ["/data", "/uploads"] });
  await expect(page.getByRole("region", { name: "Unsaved changes" }).getByText("No unsaved changes")).toBeVisible();
  const stored = (await (await page.request.get("/api/apps/crate.example.net/container")).json()) as { container_port: number; volumes: string[] };
  expect(stored).toMatchObject({ container_port: 3100, volumes: ["/data", "/uploads"] });
  // Put back, so the other theme's run finds the app as it was seeded.
  const restored = await page.request.put("/api/apps/crate.example.net/container", {
    data: { clear_registry: false, container_port: 3000, volumes: ["/data"] },
    headers: await csrf(page),
  });
  expect(restored.ok(), await restored.text()).toBe(true);
});

withCatalogApps("an image's registry account is kept without its password coming back, and can be removed", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps/registry.example.net/settings/container");
  const main = page.getByRole("main");
  await expect(main.getByLabel("Image")).toHaveValue("ghcr.io/example-org/registry-demo:1.4");
  await expect(main.getByLabel(/^Username/)).toHaveValue("ci");
  const password = main.getByLabel(/^Password/);
  await expect(password).toHaveValue("");
  await expect(password).toHaveAttribute("placeholder", "A value is kept. Leave this empty to keep it.");
  expect(await page.content()).not.toContain("seeded-registry-token-never-shown");
  await expect(main.getByRole("radio")).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "an image application's settings");
});

withCatalogApps("a Compose stack and a container have no build commands to set, and the Build page says so and keeps only the sandbox", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps/vault.example.net/settings/build");
  for (const domain of ["vault.example.net", "crate.example.net"]) {
    await page.goto(`/apps/${domain}/settings/build`);
    await expect(page.getByRole("heading", { level: 2, name: "Build", exact: true })).toBeVisible();
    await expect(page.getByText(/is built by its own configuration, so it has no build commands here/)).toBeVisible();
    await expect(page.getByRole("textbox", { name: /^Build command/ })).toHaveCount(0);
  }
});

withCatalogApps("the container settings, the build commands, the installed recipes and the image start read in Spanish", async ({ page, consoleServer }) => {
  await useSpanish(page);
  await signInSpanish(page, consoleServer, "/apps/crate.example.net/settings/container");
  await expect(page.getByRole("heading", { level: 2, name: spanish.containers.settings.title })).toBeVisible();
  await expect(page.getByText(spanish.containers.secrets.stored, { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "container settings (es)");
  await expectNoEnglishLeftovers(page, "container settings");

  await page.goto("/apps/shop.example.net/settings/build");
  await expect(page.getByRole("heading", { level: 2, name: spanish.appBuild.title })).toBeVisible();
  await expect(page.getByText(spanish.appBuild.standing.none)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "build commands (es)");
  await expectNoEnglishLeftovers(page, "build commands");

  await page.goto("/catalog/installed");
  await expect(page.getByText(spanish.catalog.installed.behind)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "installed recipes (es)");
  await expectNoEnglishLeftovers(page, "installed recipes");

  await page.goto("/apps/new");
  await page.getByRole("radiogroup", { name: spanish.newApp.modes.label }).getByRole("radio", { name: spanish.newApp.modes.image }).click();
  await expect(page.getByLabel(spanish.containers.source.image, { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the image start (es)");
  await expectNoEnglishLeftovers(page, "the image start");
});
