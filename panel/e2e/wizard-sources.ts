/**
 * Where the console server keeps the projects the new-app wizard is pointed at.
 *
 * `scripts/console_server.py` (seed_domains_and_sources) writes them into the sandbox's
 * stand-in for /var/www/src, beside its /var/www/apps. The sandbox lives in a fresh temporary
 * directory per server, so the path is read off the server's own apps directory.
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { confirmItsYou, expect } from "./fixtures";
import type { ConsoleServer, PageProblems } from "./fixtures";

/**
 * Chromium's log of the refusal a local-path inspection gets before sudo mode: reading a
 * directory on the server as root is an elevated action, so the first inspection of a session
 * is answered 403 `elevation_required` and retried after "Confirm it's you".
 */
const INSPECT_NEEDS_SUDO = /status of 403 .* \/api\/apps\/inspect$/;

/**
 * `container-api` holds a lone Dockerfile: the inspection proposes the container type for it.
 * `rust-service` is a Rust library without a Dockerfile: it has no program to run, and the verdict says why.
 * `railway-api` is a Node API with a railway.toml: the inspection proposes its settings.
 */
export type WizardSource = "storefront" | "landing" | "container-api" | "rust-service" | "railway-api";

/** The absolute path of a seeded source on the server `page` is signed in to. */
export async function wizardSource(page: Page, name: WizardSource): Promise<string> {
  const response = await page.request.get("/api/config/apps-directory");
  if (!response.ok()) throw new Error(`GET /api/config/apps-directory answered ${String(response.status())}`);
  const { apps_directory: apps } = (await response.json()) as { apps_directory: string };
  return path.posix.join(path.posix.dirname(apps), "src", name);
}

/**
 * The wizard's one box: what to deploy is pasted or typed here, and Noust tells a repository, a folder, an image
 * and a search of the catalog apart (owner item 99). The step opens on it, so there is nothing to switch to first.
 */
export async function typedSource(page: Page) {
  const field = page.getByLabel("What to deploy");
  await expect(field).toBeVisible();
  return field;
}

/**
 * Inspects a source from the wizard's first step and waits for the next one, Address,
 * confirming it's the operator when the session is not in sudo mode yet.
 */
export async function inspectSource(page: Page, server: ConsoleServer, problems: PageProblems, source: string): Promise<void> {
  problems.expect(INSPECT_NEEDS_SUDO);
  await (await typedSource(page)).fill(source);
  await page.getByRole("button", { name: "Continue" }).click();
  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  const address = page.getByRole("heading", { level: 2, name: "Address" });
  await expect(confirm.or(address)).toBeVisible();
  if (await confirm.isVisible()) await confirmItsYou(page, server);
  await expect(address).toBeFocused();
}

/** Presses the wizard's Continue and waits for the step it leads to, focused. */
export async function continueTo(page: Page, step: "Address" | "Configuration" | "Variables" | "Database" | "Deploy"): Promise<void> {
  await page.getByRole("button", { name: "Continue", exact: true }).click();
  const target = page.getByRole("heading", { level: 2, name: step });
  if (step === "Deploy") {
    // A code start on a server with a database engine running asks about a database first
    // (databases/wizard/DatabaseStep.tsx); "No database", its default, goes on unchanged.
    const database = page.getByRole("heading", { level: 2, name: "Database" });
    await expect(database.or(target)).toBeVisible();
    if (await database.isVisible()) await page.getByRole("button", { name: "Continue", exact: true }).click();
  }
  await expect(target).toBeFocused();
}

/**
 * Opens the Configuration step. A project with one detected type has nothing to ask there, so
 * Continue passes over it (checklist M-12); what was decided so far (beside the step, on the way)
 * and the summary of the deploy (at the end) say the type and offer to change it, which is where
 * this opens it from.
 */
export async function openConfiguration(page: Page): Promise<void> {
  await page.getByRole("button", { name: /^Change (detected|type)$/ }).click();
  await expect(page.getByRole("heading", { level: 2, name: "Configuration" })).toBeFocused();
}

/** The storefront's variables that have no value to fall back on, filled in or generated. */
export async function fillStorefrontVariables(page: Page): Promise<void> {
  await page.getByLabel(/^DATABASE_URL/).fill("postgres://storefront@localhost/storefront");
  for (const name of ["NEXTAUTH_SECRET", "STRIPE_SECRET_KEY", "SMTP_PASSWORD"]) {
    await page.getByRole("button", { name: `Generate ${name}` }).click();
  }
}

/** The ways of starting that are not a thing to paste or search for: folded under "Other ways to start". */
const OTHER_WAYS = new Set(["Compose", "Import an application", "Already running"]);

/**
 * Gets to one of the wizard's ways of starting by its name, the way a person does. A repository, a folder or an image
 * is not a way to choose (it goes in the box); the catalog is "Browse all recipes" under it; the rest of the ways are
 * behind "Other ways to start".
 */
export async function chooseWay(page: Page, name: string): Promise<void> {
  if (name === "From a recipe") {
    await page.getByRole("button", { name: /^Browse (all \d+ recipes|the \d+ recipe)$/ }).click();
    return;
  }
  if (OTHER_WAYS.has(name)) {
    await page.getByRole("button", { name: "Other ways to start", exact: true }).click();
    await page.getByRole("button", { name, exact: true }).click();
  }
}
