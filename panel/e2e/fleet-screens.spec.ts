/**
 * Screenshots of the fleet's console for review (@screens, left out of the default run): the
 * shell in its three contexts, every Fleet view, a bulk action's plan and job, Settings in both
 * scopes, the Add server dialog's steps and the New application wizard's Server step, at 1440,
 * 1920 and 390 wide, in the project's theme, in English and Spanish. A fleet of three: web-2
 * answers, db-1 is down, old-1 runs an older Noust.
 *
 *   NOUST_SCREENS=1 NOUST_SCREENS_DIR=/tmp/c1-screens npx playwright test fleet-screens
 *
 * It signs in and confirms sudo mode through the API, so it does not depend on the sign-in
 * page's own flow, which e2e/shell.spec.ts covers.
 */

import type { Page } from "@playwright/test";

import { expect, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";

const OUT = process.env.NOUST_SCREENS_DIR ?? "test-results/fleet-screens";
/** NOUST_SCREENS_QUICK=1: English at 1440 only, to iterate on a change. */
const QUICK = process.env.NOUST_SCREENS_QUICK === "1";
const VIEWPORTS = [
  { name: "1440", width: 1440, height: 900 },
  { name: "1920", width: 1920, height: 1080 },
  { name: "390", width: 390, height: 844 },
].filter((viewport) => (process.env.NOUST_SCREENS_WIDTH === undefined ? true : viewport.name === process.env.NOUST_SCREENS_WIDTH)).slice(0, QUICK ? 1 : 3);
const LANGUAGES = (["en", "es"] as const).slice(0, QUICK ? 1 : 2);
const NODE_APP = "node-only.example.net";

function portOf(server: ConsoleServer): string {
  return new URL(server.url).port;
}

async function apiSignIn(page: Page, server: ConsoleServer): Promise<void> {
  const response = await page.request.post(`${server.url}/api/auth/login`, {
    data: { token: server.token, totp_code: server.totpSecret === null ? null : server.secondFactor(), bearer: false },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

async function apiElevate(page: Page, server: ConsoleServer): Promise<void> {
  const cookies = await page.context().cookies(server.url);
  const csrf = cookies.find((cookie) => cookie.name === "wasm_csrf")?.value ?? "";
  const response = await page.request.post(`${server.url}/api/auth/elevate`, {
    data: server.totpSecret === null ? { token: server.token } : { code: server.secondFactor() },
    headers: { "X-WASM-CSRF": csrf },
  });
  expect(response.ok(), await response.text()).toBe(true);
}

/** A foreign but well-formed join code, for the dialog's refusal screens. */
function foreignJoinCode(): string {
  const field = (bytes: Buffer) => {
    const length = Buffer.alloc(4);
    length.writeUInt32BE(bytes.length);
    return Buffer.concat([length, bytes]);
  };
  const blob = Buffer.concat([field(Buffer.from("ssh-ed25519")), field(Buffer.alloc(32, 7))]);
  const document = {
    ssh_host_key_line: `ssh-ed25519 ${blob.toString("base64")}`,
    ssh_user: "noust-tunnel",
    ssh_port: 22,
    console_port: 8080,
    token: `noust_tok_${"a".repeat(43)}`,
    noust_version: "3.1.0",
    central_key_fp: `SHA256:${"A".repeat(43)}`,
    node_name: "web-3",
  };
  return `noust-join:v1:${Buffer.from(JSON.stringify(document)).toString("base64url")}`;
}

test("@screens the fleet's console, every context, view and dialog", async ({ page, problems }, testInfo) => {
  test.setTimeout(30 * 60_000);
  // Screens, not assertions: another workstream's page may log what its own tests cover.
  problems.expect(/./);
  const theme = testInfo.project.name;
  const nodes: RunningServer[] = await Promise.all([
    startConsoleServer([], { env: { NOUST_E2E_FLEET_APP: NODE_APP } }),
    startConsoleServer([]),
    startConsoleServer([], { env: { NOUST_E2E_OLDER_NODE: "3.0.0" } }),
  ]);
  const [up, down, older] = nodes as [RunningServer, RunningServer, RunningServer];
  const central = await startConsoleServer([
    "--totp",
    "--backup-codes",
    "500",
    "--fleet-node",
    `web-2=127.0.0.1:${portOf(up)}`,
    "--fleet-node",
    `db-1=127.0.0.1:${portOf(down)}`,
    "--fleet-node",
    `old-1=127.0.0.1:${portOf(older)}`,
  ]);
  try {
    await down.stop();
    await apiSignIn(page, central);

    for (const language of LANGUAGES) {
      await page.addInitScript((locale) => {
        window.localStorage.setItem("noust.locale", locale);
      }, language);
      for (const viewport of VIEWPORTS) {
        await page.setViewportSize({ width: viewport.width, height: viewport.height });
        const shot = async (name: string, fullPage = true): Promise<void> => {
          // Let data arrive and motion stop: the screens show the settled page. A fleet view
          // waits for its slowest server up to the central's deadline.
          await page
            .waitForFunction(() => document.querySelector('main [aria-busy="true"], [role="dialog"] [aria-busy="true"]') === null, undefined, { timeout: 25_000 })
            .catch(() => undefined);
          await page.waitForTimeout(600);
          await page.screenshot({ path: `${OUT}/${language}/${theme}/${viewport.name}/${name}.png`, fullPage });
        };
        const visit = async (path: string, name: string): Promise<void> => {
          await page.goto(`${central.url}${path}`);
          await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 20_000 });
          await shot(name);
        };

        await visit("/n/web-2/apps", "01-server-web-2-apps");
        await visit("/apps", "02-server-this-apps");
        await visit("/fleet", "03-fleet-summary");
        await visit("/fleet/servers", "04-fleet-servers");
        await visit("/fleet/apps", "05-fleet-apps");
        await visit("/fleet/certificates", "06-fleet-certificates");
        await visit("/fleet/backups", "07-fleet-backups");
        await visit("/fleet/updates", "08-fleet-updates");
        await visit("/fleet/activity", "09-fleet-activity");
        await visit("/n/web-2/settings", "10-settings-server-web-2");
        await visit("/settings/servers", "11-settings-central-servers");
        await visit("/settings/tokens", "12-settings-central-tokens");
        await visit("/settings/central", "13-settings-central-seal");
        await visit("/apps/new", "14-new-app-server-step");

        // The selector, open, from a node's page.
        await page.goto(`${central.url}/n/web-2/apps`);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 20_000 });
        await page.getByRole("button", { name: /^(Server|Servidor):/ }).click();
        await shot("15-selector-open", false);
        await page.keyboard.press("Escape");

        // A bulk action: its steps, its plan, then its job.
        await apiElevate(page, central);
        await page.goto(`${central.url}/fleet/servers`);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 20_000 });
        await page.getByRole("checkbox", { name: /web-2/ }).first().click();
        await page.getByRole("button", { name: /(Run an action on|Ejecutar una acción en) 1/ }).click();
        const bulk = page.getByRole("dialog").first();
        await bulk.getByRole("radio").first().click();
        await shot("16-bulk-action", false);
        await bulk.getByRole("button", { name: /^(Continue|Continuar)$/ }).click();
        await shot("17-bulk-servers", false);
        await bulk.getByRole("button", { name: /^(Show the plan|Ver el plan)$/ }).click();
        await expect(bulk.getByText(/«|“/).first()).toBeVisible({ timeout: 20_000 });
        await shot("18-bulk-plan", false);
        await bulk.getByRole("button", { name: /^(Run on|Ejecutar en) / }).click();
        await expect(page).toHaveURL(/\/fleet\/jobs\//, { timeout: 20_000 });
        await page.waitForTimeout(3_000);
        await shot("19-bulk-job");

        // Adding a server: the command, a token pasted by mistake, the code read, a refusal.
        await page.goto(`${central.url}/settings/servers`);
        await expect(page.getByRole("heading", { level: 1 })).toBeVisible({ timeout: 20_000 });
        await page.getByRole("button", { name: /^(Add a server|Añadir un servidor)$/ }).first().click();
        const add = page.getByRole("dialog").first();
        await add.getByRole("textbox").first().fill("web-3");
        await add.getByRole("button", { name: /^(Show the command|Mostrar la orden)$/ }).click();
        await expect(add.getByTestId("authorize-command")).toBeVisible();
        await shot("20-add-server-authorize", false);
        await add.getByRole("button", { name: /^(I ran it: continue|Ya lo ejecuté: continuar)$/ }).click();
        const code = add.locator('input[type="password"]');
        await code.fill(central.token);
        await shot("21-add-server-token-pasted", false);
        await code.fill(foreignJoinCode());
        await add.locator('input[placeholder="web2.example.com"]').fill("127.0.0.1");
        await shot("22-add-server-code-read", false);
        await add.getByRole("button", { name: /^(Add server|Añadir servidor)$/ }).click();
        await page.waitForTimeout(2_500);
        await shot("23-add-server-refused", false);
        await page.keyboard.press("Escape");
      }
    }
  } finally {
    await page.close();
    await Promise.all([up.stop(), down.stop(), older.stop(), central.stop()]);
  }
});
