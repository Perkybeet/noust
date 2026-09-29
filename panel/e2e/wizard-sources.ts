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
 * `container-api` holds a lone Dockerfile: no type matches it, and the verdict says why.
 * `railway-api` is a Node API with a railway.toml: the inspection proposes its settings.
 */
export type WizardSource = "storefront" | "landing" | "container-api" | "railway-api";

/** The absolute path of a seeded source on the server `page` is signed in to. */
export async function wizardSource(page: Page, name: WizardSource): Promise<string> {
  const response = await page.request.get("/api/config/apps-directory");
  if (!response.ok()) throw new Error(`GET /api/config/apps-directory answered ${String(response.status())}`);
  const { apps_directory: apps } = (await response.json()) as { apps_directory: string };
  return path.posix.join(path.posix.dirname(apps), "src", name);
}

/**
 * The wizard's typed source field. The seeded machine has a GitHub App, so the wizard opens on
 * "From GitHub" once it knows; this waits for that choice to appear and switches to "URL or
 * path", so the field returned is the one that stays.
 */
export async function typedSource(page: Page) {
  const modes = page.getByRole("radiogroup", { name: "Where the code is" });
  const typed = modes.getByRole("radio", { name: "URL or path" });
  // "URL or path" is there, and checked, before the wizard knows of the App; waiting for it
  // alone let the wizard switch to "From GitHub" after the check below and under the click.
  await expect(modes.getByRole("radio", { name: "From GitHub" })).toBeVisible();
  if ((await typed.getAttribute("aria-checked")) !== "true") await typed.click();
  const field = page.getByLabel("Repository or directory");
  await expect(field).toBeVisible();
  return field;
}

/**
 * Inspects a source from the wizard's first step and waits for the Review step, confirming
 * it's the operator when the session is not in sudo mode yet.
 */
export async function inspectSource(page: Page, server: ConsoleServer, problems: PageProblems, source: string): Promise<void> {
  problems.expect(INSPECT_NEEDS_SUDO);
  await (await typedSource(page)).fill(source);
  await page.getByRole("button", { name: "Inspect source" }).click();
  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  const review = page.getByRole("heading", { level: 2, name: "Review" });
  await expect(confirm.or(review)).toBeVisible();
  if (await confirm.isVisible()) await confirmItsYou(page, server);
  await expect(review).toBeFocused();
}
