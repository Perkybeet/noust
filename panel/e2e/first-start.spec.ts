/**
 * The sign-in of a server that has no account yet, against the real backend (item 198): it opens on the access token, because there is no
 * account to type, and says where the token was shown. A server installed by the installer says the installer printed it; one in a
 * container (the NAS's app store) says it is in the container's log, and the command that issues another is the one run through
 * `docker exec`. Once an account exists the server says nothing of the first start and the page says what it always said.
 *
 * The `--empty` server has no account and is not in a container, so it says "installer"; the sandbox is never inside a container, so the
 * container's answer is the real server's answer with `first_start` as a container's server gives it. With `NOUST_SCREENS=1` the three
 * cases are written for review, at 1440x900 and 390 wide, into $NOUST_FIRST_START_SCREENS.
 */

import type { Page } from "@playwright/test";
import path from "node:path";

import { expect, expectNoA11yViolations, settle, startConsoleServer, test } from "./fixtures";
import type { RunningServer } from "./fixtures";

const OUT = process.env.NOUST_FIRST_START_SCREENS ?? path.join(import.meta.dirname, "__screens__", "first-start");
const PHONE = { width: 390, height: 844 };

let empty: RunningServer;
let withAccounts: RunningServer;

test.beforeAll(async () => {
  [empty, withAccounts] = await Promise.all([startConsoleServer(["--empty"]), startConsoleServer([])]);
});

test.afterAll(async () => {
  await Promise.all([empty.stop(), withAccounts.stop()]);
});

/** Makes the server answer as the one of a container does: the token is in its log. */
async function asContainer(page: Page): Promise<void> {
  await page.route("**/api/auth/session", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, unknown>;
    await route.fulfill({ response, json: { ...body, first_start: { token_shown_by: "container_log" } } });
  });
}

/** The sandbox has no account, so the answer of a server that has some is the real one with the two words gone, as the server drops them. */
async function asAServerWithAccounts(page: Page): Promise<void> {
  await page.route("**/api/auth/session", async (route) => {
    const response = await route.fetch();
    const body = (await response.json()) as Record<string, unknown>;
    await route.fulfill({ response, json: { ...body, accounts_exist: null, first_start: null } });
  });
}

const CASES = {
  installer: { arrive: (): Promise<void> => Promise.resolve(), url: () => empty.url },
  container: { arrive: asContainer, url: () => empty.url },
  accounts: { arrive: asAServerWithAccounts, url: () => withAccounts.url },
} as const;

async function open(page: Page, which: keyof typeof CASES): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 900 });
  await CASES[which].arrive(page);
  await page.goto(`${CASES[which].url()}/login`);
}

async function shoot(page: Page, name: string, projectName: string): Promise<void> {
  await page.setViewportSize({ width: 1440, height: 900 });
  await settle(page);
  await page.waitForTimeout(400);
  await page.screenshot({ path: path.join(OUT, projectName, `${name}-1440.png`) });
  await page.setViewportSize(PHONE);
  await settle(page);
  await page.waitForTimeout(300);
  await page.screenshot({ path: path.join(OUT, projectName, `${name}-390.png`) });
}

test("a server with no account, installed by the installer, opens on the token the installer printed", async ({ page }) => {
  await open(page, "installer");
  await expect(page.getByRole("heading", { level: 1, name: "Sign in with the access token" })).toBeVisible();
  await expect(page.getByText("The installer printed it once, in a box. Paste it here.")).toBeVisible();
  await expect(page.getByText("noust web token --new")).toBeVisible();
  await expect(page.getByLabel("Username or email")).toHaveCount(0);
  // The account form asks for what nobody has yet: no way back to it.
  await expect(page.getByRole("button", { name: "With a password" })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "the token of a server with no account, from the installer");
});

test("a server with no account, in a container, says the token is in the container's log and names the installer nowhere", async ({ page }) => {
  await open(page, "container");
  await expect(page.getByRole("heading", { level: 1, name: "Sign in with the access token" })).toBeVisible();
  await expect(page.getByText(/Noust wrote it once in the container's log, in a box, the first time it started/)).toBeVisible();
  await expect(page.getByText("docker exec -it <container> noust web token --new")).toBeVisible();
  await expect(page.getByText(/installer/i)).toHaveCount(0);
  await expect(page.getByRole("button", { name: "With a password" })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "the token of a server with no account, in a container");
});

test("a server that has accounts opens on the account form, and says what it always said", async ({ page }) => {
  await open(page, "accounts");
  await expect(page.getByLabel("Username or email")).toBeVisible();
  await expect(page.getByText(/First time on this server\? Sign in with the access token the installer printed/)).toBeVisible();
  await expect(page.getByText("noust web token --new")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the sign-in of a server that has accounts");
});

if (process.env.NOUST_SCREENS === "1") {
  /** The three cases at 1440x900 and 390 wide, in the theme of the project. */
  test.describe("the sign-in of a first start @screens", () => {
    for (const [which, name, heading] of [
      ["installer", "entrada-instalador", "Sign in with the access token"],
      ["container", "entrada-contenedor", "Sign in with the access token"],
      ["accounts", "entrada-con-cuentas", /Sign in/],
    ] as const) {
      test(`first start: ${which}`, async ({ page }, testInfo) => {
        await open(page, which);
        await expect(page.getByRole("heading", { level: 1, name: heading })).toBeVisible();
        await shoot(page, name, testInfo.project.name);
      });
    }
  });
}
