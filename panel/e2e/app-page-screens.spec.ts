/**
 * Screenshots of the application page for review: what the header says on every tab, the
 * overview of an app that is healthy, failed, stopped and building, the deployments list, a
 * failed deployment, the rollback question and the variables, in both themes (the light and
 * dark projects), at 1440x900 and 390x844, and in Spanish. Tagged @screens, so left out of the
 * default run; write them with
 *
 *     NOUST_SCREENS=1 npx playwright test e2e/app-page-screens.spec.ts
 *
 * into $NOUST_APP_SCREENS (default /tmp/console-app/<theme>/). Each is one landscape screen,
 * not a strip of the whole page: what an operator sees without scrolling is what is judged.
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { expect, settle, signIn, test } from "./fixtures";

const OUT = process.env.NOUST_APP_SCREENS ?? "/tmp/console-app";

const DESKTOP = { width: 1440, height: 900 };
const PHONE = { width: 390, height: 844 };

interface DeploymentRow {
  id: number;
  status: string;
  error: string | null;
  rollback_available: boolean;
}

async function deploymentId(page: Page, domain: string, pick: (row: DeploymentRow) => boolean): Promise<number> {
  const response = await page.request.get(`/api/deployments?domain=${domain}&limit=50`);
  const row = ((await response.json()) as { items: DeploymentRow[] }).items.find(pick);
  if (!row) throw new Error(`No seeded deployment of ${domain} matches`);
  return row.id;
}

interface Screen {
  name: string;
  path: (page: Page) => Promise<string> | string;
  /** What to do once the page is up: open the dialog the screenshot is about. */
  ready?: (page: Page) => Promise<void>;
}

const SCREENS: readonly Screen[] = [
  { name: "overview-healthy", path: () => "/apps/shop.example.net" },
  { name: "overview-releases", path: () => "/apps/tienda.example.org" },
  { name: "overview-failed", path: () => "/apps/clientes.example.com" },
  { name: "overview-static", path: () => "/apps/bodas.example.com" },
  { name: "deployments", path: () => "/apps/tienda.example.org/deployments" },
  { name: "deployments-in-place", path: () => "/apps/pedidos.example.org/deployments" },
  {
    name: "deployment-failed",
    path: async (page) => `/apps/tienda.example.org/deployments/${String(await deploymentId(page, "tienda.example.org", (row) => row.status === "failed" && (row.error ?? "").includes("Type error")))}`,
  },
  {
    // The build tool coloured its output: the excerpt shows its words, never the escape codes.
    name: "deployment-failed-coloured",
    path: async (page) => `/apps/clientes.example.com/deployments/${String(await deploymentId(page, "clientes.example.com", (row) => row.status === "failed"))}`,
  },
  {
    name: "deployment-live",
    path: async (page) => `/apps/tienda.example.org/deployments/${String(await deploymentId(page, "tienda.example.org", (row) => row.status === "success"))}`,
  },
  {
    name: "rollback",
    path: () => "/apps/tienda.example.org",
    ready: async (page) => {
      await page.locator("main header").getByRole("button", { name: "Roll back", exact: true }).click();
      await expect(page.getByRole("alertdialog")).toBeVisible();
    },
  },
  {
    name: "rollback-in-place",
    path: () => "/apps/pedidos.example.org",
    ready: async (page) => {
      await page.locator("main header").getByRole("button", { name: "Roll back", exact: true }).click();
      await expect(page.getByRole("alertdialog")).toBeVisible();
    },
  },
  { name: "variables", path: () => "/apps/pedidos.example.org/environment" },
  {
    name: "variable-hidden",
    path: () => "/apps/pedidos.example.org/environment",
    ready: async (page) => {
      await page.getByRole("table", { name: /^Environment variables of/ }).getByText("JWT_SECRET", { exact: true }).click();
      await expect(page.getByRole("dialog", { name: "Edit JWT_SECRET" })).toBeVisible();
    },
  },
  { name: "domains", path: () => "/apps/shop.example.net/domains" },
];

test.describe("the application page @screens", () => {
  for (const screen of SCREENS) {
    test(`app page ${screen.name}`, async ({ page, consoleServer }, testInfo) => {
      const dir = path.join(OUT, testInfo.project.name);
      await page.setViewportSize(DESKTOP);
      await signIn(page, consoleServer, "/apps");
      await page.goto(await screen.path(page));
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await screen.ready?.(page);
      await settle(page);
      await page.waitForTimeout(600);
      await page.screenshot({ path: path.join(dir, `${screen.name}-1440.png`) });

      await page.setViewportSize(PHONE);
      await settle(page);
      await page.waitForTimeout(400);
      await page.screenshot({ path: path.join(dir, `${screen.name}-390.png`) });
    });
  }

  test("app page in Spanish", async ({ page, consoleServer }, testInfo) => {
    const dir = path.join(OUT, testInfo.project.name);
    await page.setViewportSize(DESKTOP);
    await signIn(page, consoleServer, "/apps");
    await page.evaluate(() => {
      window.localStorage.setItem("noust.locale", "es");
    });
    for (const [name, route] of [
      ["es-overview", "/apps/tienda.example.org"],
      ["es-deployments", "/apps/tienda.example.org/deployments"],
      ["es-variables", "/apps/pedidos.example.org/environment"],
    ] as const) {
      await page.goto(route);
      await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
      await settle(page);
      await page.waitForTimeout(600);
      await page.screenshot({ path: path.join(dir, `${name}-1440.png`) });
    }
  });
});
