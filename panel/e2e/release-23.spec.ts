/**
 * The 2.3 screens against the real backend, each state through axe, in both themes:
 *
 * - a recipe from the wizard's gallery (WordPress), reviewed and deployed to its end, with
 *   the recipe's next steps (the console server answers wordpress.org's archive and its SHA-1
 *   itself and models PHP-FPM, so nothing reaches the network);
 * - a repository with a Railway configuration, whose proposal is shown and whose health check
 *   reaches the create request;
 * - an application exported from its settings, with and without its secret values (sudo mode);
 * - that export imported on another domain, with the secret it left out, and the job's report
 *   of what was applied and what was not;
 * - the console's language, switched in Settings and kept across a reload;
 * - the language of notifications, saved to the server's configuration.
 */

import type { Download, Page } from "@playwright/test";
import { readFile, writeFile } from "node:fs/promises";

import { csrf, forgetApp } from "./apps-cleanup";
import { spanish } from "./catalog";
import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, stillness, test } from "./fixtures";
import { inspectSource, wizardSource } from "./wizard-sources";

/** The seeded application with an alias, a cron job, a secret variable and a linked database. */
const EXPORTED = "lanzamiento.example.org";

/** Its secret variable, and the value the seed gave it. */
const SECRET = { name: "NEWSLETTER_API_KEY", value: "nl_live_8f2c61d0b7a94e3f" };

interface ExportDocument {
  format: string;
  version: number;
  secrets_included: boolean;
  app: { domain: string; app_type: string; keep_releases: number | null };
  domains: { aliases: string[] };
  env: Record<string, { secret: boolean; value: string | null }>;
  cron: { name: string }[];
  databases: { engine: string; name: string | null }[];
}

async function downloadedJson(download: Download): Promise<ExportDocument> {
  const path = await download.path();
  return JSON.parse(await readFile(path, "utf8")) as ExportDocument;
}

/** Chooses one of the wizard's starts: "From a recipe", "Import an application"... */
async function chooseStart(page: Page, name: string): Promise<void> {
  const choice = page.getByRole("radiogroup", { name: "Where the code is" }).getByRole("radio", { name });
  await expect(choice).toBeVisible();
  await choice.click();
  await expect(choice).toHaveAttribute("aria-checked", "true");
}

test("a recipe from the gallery is reviewed, deployed, and says what to do next", async ({ page, consoleServer }) => {
  test.setTimeout(180_000);
  const domain = "blog-wp.example.net";
  await signIn(page, consoleServer, "/apps/new");
  await chooseStart(page, "From a recipe");

  // GET /api/recipes, answered from the recipe files the package ships.
  const recipes = page.getByRole("list", { name: "Recipes" });
  for (const title of ["WordPress", "Uptime Kuma", "Umami", "n8n"]) {
    await expect(recipes.getByRole("button", { name: `Use ${title}` })).toBeEnabled();
  }
  // Ghost needs MySQL 8 and Plausible ClickHouse: listed, and said to be unavailable.
  await expect(recipes.getByText("Not available in this release")).toHaveCount(2);
  await settle(page);
  await expectNoA11yViolations(page, "the recipe gallery");

  await recipes.getByRole("button", { name: "Use WordPress" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Review" })).toBeFocused();
  await expect(page.getByText("Noust deploys WordPress as its recipe describes.", { exact: false })).toBeVisible();
  // The archive and where its checksum is published, straight from wordpress.yaml.
  await expect(page.getByText("https://wordpress.org/latest.tar.gz", { exact: true })).toBeVisible();
  await expect(page.getByText("A new mysql database and user, their credentials in the app's .env")).toBeVisible();
  await page.getByLabel("Domain", { exact: true }).fill(domain);
  await expect(page.getByText(`${domain} points here`)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the WordPress review");

  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Deploy" })).toBeFocused();
  await stillness(page);
  await expectNoA11yViolations(page, "the WordPress deploy step");

  const queued = page.waitForRequest((request) => request.url().endsWith("/api/apps") && request.method() === "POST");
  await page.getByRole("button", { name: `Deploy ${domain}` }).click();
  const body = (await queued).postDataJSON() as Record<string, unknown>;
  expect(body).toMatchObject({ domain, recipe: "wordpress" });
  expect(body).not.toHaveProperty("source");

  // A recipe's deploy is followed on the wizard's page to its end: the notes come with the result.
  await expect(page.getByText(`${domain} is deployed`)).toBeVisible({ timeout: 90_000 });
  const next = page.getByRole("region", { name: "Next steps" });
  await expect(next.getByRole("listitem")).toHaveCount(3);
  // Its address is a link; HTTPS was left on, so the notes say https.
  await expect(next.getByRole("link", { name: new RegExp(`^https://${domain.replace(/\./g, "\\.")}/wp-admin/install\\.php`) })).toBeVisible();
  await expect(page.getByRole("link", { name: `Open ${domain}` })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a deployed recipe's next steps");

  // What the deploy recorded: a PHP-FPM app, its database linked.
  const app = (await (await page.request.get(`/api/apps/${domain}`)).json()) as { app_type: string };
  expect(app.app_type).toBe("php-fpm");

  await forgetApp(page, consoleServer, domain);
});

test("a repository configured for Railway gets its proposal, and its health check reaches the deploy", async ({ page, consoleServer, problems }) => {
  // The deploy runs to its end (a failed health check: nothing listens in the sandbox).
  test.setTimeout(180_000);
  const domain = "api-railway.example.net";
  await signIn(page, consoleServer, "/apps/new");
  await inspectSource(page, consoleServer, problems, await wizardSource(page, "railway-api"));

  const proposal = page.getByRole("region", { name: /^Found a Railway configuration/ });
  await expect(proposal).toContainText("railway.toml");
  await expect(proposal.getByText("GET /healthz, waiting up to 60 s")).toBeVisible();
  await expect(proposal.getByText("npm run build", { exact: true })).toBeVisible();
  // What has no equivalent here, in the server's words.
  await expect(proposal.getByText(/^restartPolicyType is ON_FAILURE/)).toBeVisible();
  await expect(proposal.getByRole("checkbox", { name: "Use what it proposes" })).toBeChecked();
  await page.getByLabel("Domain", { exact: true }).fill(domain);
  await expect(page.getByText(`${domain} points here`)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a Railway proposal on the review");

  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Deploy" })).toBeFocused();
  const queued = page.waitForRequest((request) => request.url().endsWith("/api/apps") && request.method() === "POST");
  await page.getByRole("button", { name: `Deploy ${domain}` }).click();
  const body = (await queued).postDataJSON() as Record<string, unknown>;
  expect(body).toMatchObject({ domain, app_type: "nodejs", health_path: "/healthz", health_timeout: 60 });

  await expect(page).toHaveURL(new RegExp(`/apps/${domain.replace(/\./g, "\\.")}/deployments/\\d+$`), { timeout: 30_000 });
  await forgetApp(page, consoleServer, domain);
});

test("an application exports from its settings, with its secret values only after sudo mode", async ({ page, consoleServer, problems }) => {
  // Secret values need a recent confirmation: the first request is refused, by design.
  problems.expect(new RegExp(`status of 403 .* /api/apps/${EXPORTED.replace(/\./g, "\\.")}/export$`));
  await signIn(page, consoleServer, `/apps/${EXPORTED}/settings`);
  const section = page.getByRole("region", { name: "Export this application" });
  await expect(section).toBeVisible();
  await section.scrollIntoViewIfNeeded();
  await settle(page);
  await expectNoA11yViolations(page, "the export section");

  const plain = page.waitForEvent("download");
  await section.getByRole("button", { name: "Export application" }).click();
  const without = await plain;
  expect(without.suggestedFilename()).toBe(`${EXPORTED}.wasm-app.json`);
  const document = await downloadedJson(without);
  expect(document).toMatchObject({ format: "wasm-app", version: 1, secrets_included: false });
  expect(document.app).toMatchObject({ domain: EXPORTED, app_type: "static", keep_releases: 3 });
  expect(document.domains.aliases).toEqual([`www.${EXPORTED}`]);
  expect(document.env[SECRET.name]).toEqual({ secret: true, value: null });
  expect(document.env.SITE_NAME).toEqual({ secret: false, value: "Lanzamiento" });
  expect(document.cron.map((job) => job.name)).toEqual(["lanzamiento-sitemap"]);
  await expect(page.locator(".toast").filter({ hasText: `Exported ${EXPORTED}` })).toBeVisible();

  await section.getByRole("checkbox", { name: "Include secret values" }).check();
  await stillness(page);
  await expectNoA11yViolations(page, "the export with secret values chosen");
  const secret = page.waitForEvent("download");
  await section.getByRole("button", { name: "Export application" }).click();
  await confirmItsYou(page, consoleServer);
  const withSecrets = await downloadedJson(await secret);
  expect(withSecrets.secrets_included).toBe(true);
  expect(withSecrets.env[SECRET.name]).toEqual({ secret: true, value: SECRET.value });
});

test("an export imports on another domain with the secret it left out, and says what it applied", async ({ page, consoleServer, problems }, testInfo) => {
  test.setTimeout(180_000);
  // Importing deploys as root: sudo mode first.
  problems.expect(/status of 403 .* \/api\/apps\/import$/);
  const domain = "copia.example.org";
  await signIn(page, consoleServer, "/apps/new");
  // The seeded application's export, as `noust app export` or its settings would write it.
  const exported = await page.request.get(`/api/apps/${EXPORTED}/export`);
  expect(exported.ok(), await exported.text()).toBe(true);
  const file = testInfo.outputPath(`${EXPORTED}.wasm-app.json`);
  await writeFile(file, JSON.stringify(await exported.json(), null, 2));

  await chooseStart(page, "Import an application");
  await settle(page);
  await expectNoA11yViolations(page, "the import start");
  await page.getByLabel("Export file").setInputFiles(file);
  await expect(page.getByRole("status").filter({ hasText: `Read ${EXPORTED}.wasm-app.json` })).toBeVisible();
  await expect(page.getByText(new RegExp(`^An export of ${EXPORTED.replace(/\./g, "\\.")}, a .+ app\\.$`))).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "an export file read");

  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Review" })).toBeFocused();
  const domainField = page.getByLabel("Domain", { exact: true });
  await expect(domainField).toHaveValue(EXPORTED);
  await domainField.fill(domain);
  const secretField = page.getByLabel(new RegExp(`^${SECRET.name}`));
  await expect(secretField).toHaveValue("");
  await secretField.fill("nl_live_copy_5b1e");
  await settle(page);
  await expectNoA11yViolations(page, "the import review");

  await page.getByRole("button", { name: "Continue" }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Deploy" })).toBeFocused();
  await stillness(page);
  await expectNoA11yViolations(page, "the import's deploy step");

  const queued = page.waitForRequest((request) => request.url().endsWith("/api/apps/import") && request.method() === "POST");
  await page.getByRole("button", { name: `Import ${domain}` }).click();
  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  const importing = page.getByText(new RegExp(`^(Importing ${domain.replace(/\./g, "\\.")}|${domain.replace(/\./g, "\\.")} is imported)$`));
  await expect(confirm.or(importing)).toBeVisible();
  if (await confirm.isVisible()) await confirmItsYou(page, consoleServer);
  const body = (await queued).postDataJSON() as { domain: string; env: Record<string, string>; document: ExportDocument };
  expect(body.domain).toBe(domain);
  expect(body.env).toEqual({ [SECRET.name]: "nl_live_copy_5b1e" });
  expect(body.document.app.domain).toBe(EXPORTED);

  await expect(page.getByText(`${domain} is imported`)).toBeVisible({ timeout: 90_000 });
  const applied = page.getByRole("region", { name: "Applied", exact: true });
  const notApplied = page.getByRole("region", { name: "Not applied" });
  await expect(applied.getByText(`alias www.${domain}`)).toBeVisible();
  await expect(applied.getByText("keep 3 releases")).toBeVisible();
  await expect(applied.getByText("secret marks")).toBeVisible();
  // The cron job keeps its name, which the exported application still has on this server;
  // the database is named by the export, never recreated. Both say why, in the server's words.
  await expect(notApplied.getByText("cron lanzamiento-sitemap")).toBeVisible();
  await expect(notApplied.getByText("database lanzamiento_newsletter (postgresql)")).toBeVisible();
  await expect(notApplied.getByText(/^Databases are not exported, only named\./)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "an import's report");

  await forgetApp(page, consoleServer, domain);
});

test("the language chosen in Settings applies at once and survives a reload", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/settings");
  const language = page.getByRole("main").getByRole("group", { name: "Language" });
  await expect(language.getByRole("button", { name: "English" })).toHaveAttribute("aria-pressed", "true");
  await settle(page);
  await expectNoA11yViolations(page, "the language switch in English");

  await language.getByRole("button", { name: "Español" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "es");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(spanish.settings.page.title);
  await settle(page);
  await expectNoA11yViolations(page, "the language switch in Spanish");

  await page.reload();
  await expect(page.locator("html")).toHaveAttribute("lang", "es");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText(spanish.settings.page.title);
  const switched = page.getByRole("main").getByRole("group", { name: spanish.language.label });
  await expect(switched.getByRole("button", { name: "Español" })).toHaveAttribute("aria-pressed", "true");
  expect(await page.evaluate(() => window.localStorage.getItem("noust.locale"))).toBe("es");

  // Back to English, for the rest of this browser's tests.
  await switched.getByRole("button", { name: "English" }).click();
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
});

test("the language of notifications saves to the server's configuration", async ({ page, consoleServer, problems }) => {
  // Writing configuration asks "Confirm it's you" by answering 403 first, by design.
  problems.expect(/status of 403 .* \/api\/config$/);
  await signIn(page, consoleServer, "/settings/notifications");
  const section = page.getByRole("region", { name: "Language of notifications" });
  const picker = section.getByRole("radiogroup", { name: "Language of notifications" });
  await expect(picker.getByRole("radio", { name: "English" })).toBeChecked();
  await section.scrollIntoViewIfNeeded();
  await settle(page);
  await expectNoA11yViolations(page, "the language of notifications");

  await picker.getByRole("radio", { name: "Español" }).click();
  await expect(section.getByText("noust config set notifications.language es")).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the language of notifications, changed");
  const saved = page.waitForResponse((response) => response.url().endsWith("/api/config") && response.request().method() === "PATCH" && response.ok());
  await section.getByRole("button", { name: /^Save/ }).click();
  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  await expect(confirm.or(page.locator(".toast").filter({ hasText: "Saved the language of notifications" }))).toBeVisible();
  if (await confirm.isVisible()) await confirmItsYou(page, consoleServer);
  await saved;
  await expect(page.locator(".toast").filter({ hasText: "Saved the language of notifications" })).toBeVisible();

  const config = (await (await page.request.get("/api/config")).json()) as { config: { notifications?: { language?: string } } };
  expect(config.config.notifications?.language).toBe("es");
  await page.reload();
  await expect(page.getByRole("region", { name: "Language of notifications" }).getByRole("radio", { name: "Español" })).toBeChecked();

  // Back to English, for the rest of this worker's tests (still in sudo mode).
  const restored = await page.request.patch("/api/config", { data: { path: "notifications.language", value: "en" }, headers: await csrf(page) });
  expect(restored.ok(), await restored.text()).toBe(true);
});
