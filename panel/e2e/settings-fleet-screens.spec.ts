/**
 * Screenshots of Settings around the central, built (item 166), for review (@screens, left out of the default run):
 * what the central carries in its sections (channels, rules, destinations, registries, variables, the console address and
 * the policy), the panel of a setting, what saving says it will do, the table of servers for what is each server's own,
 * the tokens and the accounts, a server's page with its decision and the servers' list, what moved to Server, and a lone
 * server; at 1440x900 and at 390, in the project's theme, on a fleet of three servers beside the central: web-01 follows
 * with one value of its own, web-02 does not answer, and vps-03 is independent.
 *
 *   NOUST_SCREENS=1 NOUST_SCREENS_DIR=/tmp/ajustes-built npx playwright test settings-fleet-screens --workers=1
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { csrf } from "./apps-cleanup";
import { expect, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";

const OUT = process.env.NOUST_SCREENS_DIR ?? path.join(import.meta.dirname, "__screens__", "settings-fleet");
const SIZES = [
  { name: "1440", width: 1440, height: 900 },
  { name: "390", width: 390, height: 844 },
] as const;
const NAMES = ["web-01", "web-02", "vps-03"] as const;

interface FleetFixtures {
  nodes: RunningServer[];
  central: ConsoleServer;
}

const withFleet = test.extend<object, FleetFixtures>({
  nodes: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const started: RunningServer[] = [];
      try {
        while (started.length < NAMES.length) started.push(await startConsoleServer([]));
        await use(started);
      } finally {
        for (const node of started) await node.stop();
      }
    },
    { scope: "worker", timeout: 240_000 },
  ],
  central: [
    async ({ nodes }, use) => {
      const args = ["--totp", "--backup-codes", "400"];
      nodes.forEach((node, index) => args.push("--fleet-node", `${NAMES[index] ?? "x"}=127.0.0.1:${new URL(node.url).port}`));
      const central = await startConsoleServer(args);
      try {
        await use(central);
      } finally {
        await central.stop();
      }
    },
    { scope: "worker", timeout: 120_000 },
  ],
  baseURL: async ({ central }, use) => {
    await use(central.url);
  },
});

async function shot(page: Page, theme: string, size: string, name: string): Promise<void> {
  await settle(page);
  await page.screenshot({ path: path.join(OUT, `${name}-${size}-${theme}.png`) });
}

async function elevate(page: Page, server: ConsoleServer): Promise<void> {
  const answer = await page.request.post("/api/auth/elevate", { data: { code: server.secondFactor() }, headers: await csrf(page) });
  expect(answer.ok()).toBe(true);
}

/** What the central carries, made through its own API so every size and theme starts from the same fleet. */
async function seed(page: Page, central: ConsoleServer): Promise<void> {
  await elevate(page, central);
  const put = async (kind: string, name: string, value: Record<string, unknown>, scope = "fleet"): Promise<void> => {
    const answer = await page.request.put(`/api/fleet/settings/${kind}/${name}`, { data: { value, scope, apply: false }, headers: await csrf(page) });
    expect(answer.ok(), `${kind}/${name}: ${await answer.text()}`).toBe(true);
  };
  await put("variable", "TZ", { value: "Europe/Madrid", secret: false });
  await put("variable", "SMTP_HOST", { value: "smtp.example.net", secret: false });
  await put("variable", "SENTRY_DSN", { value: "https://sentry.example.net/1", secret: true });
  await put("variable", "SMTP_HOST", { value: "smtp2.example.net", secret: false }, "node:web-01");
  await put("channel", "slack-deploys", { type: "slack", enabled: true, settings: { channel: "#deploys" } });
  await put("channel", "correo-equipo", { type: "email", enabled: true });
  await put("alert_rule", "disco-alto", { metric: "disk", comparator: ">", threshold: 85, enabled: true });
  await put("backup_destination", "nas-offsite", { backend: "s3", fields: { bucket: "copias-acme" } });
  await put("registry", "ghcr-acme", { kind: "ghcr", username: "acme-bot", password: "not-a-real-password" });
  await put("console_link", "console", { url: "https://consola.example.com" });
  await page.request.put("/api/fleet/servers/vps-03/settings-mode", { data: { mode: "independent", adopt: false }, headers: await csrf(page) });
}

for (const size of SIZES) {
  withFleet(`@screens settings around the central at ${size.name}`, async ({ page, central, nodes, problems }, testInfo) => {
    test.setTimeout(420_000);
    // The browser logs each call to the server that is down as an error; the page says it, and that is what these shots are for.
    problems.expect(/status of 502 .* \/api\/nodes\/web-02\//);
    const theme = testInfo.project.name;
    await page.setViewportSize({ width: size.width, height: size.height });
    await signIn(page, central, "/settings");
    await seed(page, central);
    // web-02 does not answer: the central says it, and what waits for it.
    await nodes[1]?.stop();

    // Each screen, and the heading of what the page adds to its own (the shot is taken with it in view).
    const screens: [string, string, string, string?][] = [
      ["01-canales", "/settings/notifications", "Notification channels", "Channels for the whole fleet"],
      ["02-variables", "/settings/variables", "Variables", "Variables for the whole fleet"],
      ["03-reglas", "/alerts", "Alerts", "Rules for the whole fleet"],
      ["04-destinos", "/backups/destinations", "Backups", "Destinations for the whole fleet"],
      ["05-registros", "/settings/registries", "Image registries", "Accounts for the whole fleet"],
      ["06-consola", "/settings/console", "Console address", "Address for the links of notices"],
      ["07-general", "/settings/general", "General", "Policy for the whole fleet"],
      ["08-tokens", "/settings/tokens", "API tokens", "Each server's tokens are managed on that server"],
      ["09-cuentas", "/settings/accounts", "Accounts", "Each server's accounts are managed on that server"],
      ["10-envio-registros", "/settings/log-shipping", "Logs and metrics export", "The other servers"],
      ["11-secretos", "/settings/secret-managers", "Secret managers", "The other servers"],
      ["12-servidores", "/fleet/servers", "Servers"],
      ["13-servidor-carpetas", "/server/folders", "Folders and builds"],
      ["14-servidor-actualizaciones", "/server/updates", "Updates", "Automatic updates of Noust"],
    ];
    for (const [name, url, heading, adds] of screens) {
      await page.goto(url);
      await expect(page.getByRole("heading", { level: 1, name: new RegExp(`^${heading}`) })).toBeVisible();
      if (adds !== undefined) {
        await expect(page.getByText(adds, { exact: true }).first()).toBeVisible();
        // The first shot is the page as it opens; the second has what it adds in view, where the page is longer than the screen.
        await shot(page, theme, size.name, name);
        await page
          .getByText(adds, { exact: true })
          .first()
          .evaluate((element) => {
            element.scrollIntoView({ block: "start" });
          });
        await shot(page, theme, size.name, `${name}-abajo`);
      } else await shot(page, theme, size.name, name);
    }

    // The panel of a setting, and what saving it says it will do.
    await page.goto("/settings/variables");
    await page.getByRole("button", { name: "Actions for TZ" }).click();
    await page.getByRole("menuitem", { name: "See by server" }).click();
    await expect(page.getByRole("dialog", { name: "TZ" })).toBeVisible();
    await shot(page, theme, size.name, "15-panel");
    await page.keyboard.press("Escape");
    await page.getByRole("button", { name: "Actions for TZ" }).click();
    await page.getByRole("menuitem", { name: "Edit for the whole fleet…" }).click();
    await expect(page.getByRole("dialog", { name: "Edit TZ for the fleet" }).getByRole("group", { name: "What happens when it is saved" })).toBeVisible();
    await shot(page, theme, size.name, "16-guardar");
    await page.keyboard.press("Escape");

    // The selector as a label, and why.
    await page.goto("/settings/notifications");
    await page.getByRole("button", { name: "Why it is not chosen here" }).click();
    await expect(page.getByText("What is here belongs to the central")).toBeVisible();
    await shot(page, theme, size.name, "17-etiqueta");
    await page.keyboard.press("Escape");

    // A server's page: its decision, and what will change before it does.
    await page.goto("/n/web-01/server/central");
    await expect(page.getByRole("heading", { level: 2, name: "The fleet's settings" })).toBeVisible();
    await shot(page, theme, size.name, "18-ficha-sigue");
    await page.getByRole("radio", { name: /^Independent/ }).click();
    await expect(page.getByText("web-01 will stop getting settings")).toBeVisible();
    await shot(page, theme, size.name, "19-ficha-independiente");
    await page.goto("/n/vps-03/server/central");
    await expect(page.getByRole("heading", { level: 2, name: "The fleet's settings" })).toBeVisible();
    await page.getByRole("radio", { name: /^Follows the fleet's settings/ }).click();
    await shot(page, theme, size.name, "20-ficha-volver");
    await page.goto("/n/web-02/server/central");
    await expect(page.getByText("web-02 does not answer")).toBeVisible();
    await shot(page, theme, size.name, "21-ficha-sin-conexion");

    // What each server holds of its own: the table, and the page of one server's.
    await page.goto("/n/web-01/settings/log-shipping");
    await expect(page.getByRole("link", { name: "Back to all servers" })).toBeVisible();
    await shot(page, theme, size.name, "22-envio-de-un-servidor");
  });

  test(`@screens a lone server at ${size.name}`, async ({ page, consoleServer }, testInfo) => {
    test.setTimeout(240_000);
    const theme = testInfo.project.name;
    await page.setViewportSize({ width: size.width, height: size.height });
    await signIn(page, consoleServer, "/settings/notifications");
    await shot(page, theme, size.name, "30-solo-canales");
    await page.goto("/settings/variables");
    await shot(page, theme, size.name, "31-solo-variables");
    await page.goto("/settings/general");
    await shot(page, theme, size.name, "32-solo-general");
    await page.goto("/settings/tokens");
    await shot(page, theme, size.name, "33-solo-tokens");
    await page.goto("/server");
    await shot(page, theme, size.name, "34-solo-servidor");
  });
}
