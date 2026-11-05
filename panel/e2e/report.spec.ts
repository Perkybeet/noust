/**
 * "Report a problem" in a browser (item 127), against the real backend, in both themes: it opens from the help
 * menu and, on a phone, from "More"; it goes through its three steps with the real support report of the server;
 * a picture is chosen and rides in the file; and the one press at the end downloads a zip that a stock unzipper
 * accepts and opens GitHub's form with its fields filled in. Noust sends nothing: GitHub is answered by a stub here.
 * It also opens from the error screen of a page that broke, with the error already inside. Axe and the strict
 * policy watch every step.
 */

import type { Page } from "@playwright/test";
import { execFileSync } from "node:child_process";
import { mkdtempSync } from "node:fs";
import { tmpdir } from "node:os";
import path from "node:path";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

// A 1x1 PNG.
const PNG = Buffer.from("iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGD4DwABBAEAwS2OUAAAAABJRU5ErkJggg==", "base64");

function python(): string {
  return process.env.NOUST_PYTHON ?? path.join(import.meta.dirname, "..", "..", ".venv", "bin", "python");
}

/** Stands in for GitHub: the new tab is answered here, and nothing leaves the machine. */
async function stubGithub(page: Page): Promise<void> {
  await page.context().route("https://github.com/**", (route) => route.fulfill({ status: 200, contentType: "text/html", body: "<title>GitHub</title>" }));
}

async function openFromHelp(page: Page) {
  await page.getByRole("button", { name: "Help" }).click();
  await page.getByRole("menuitem", { name: "Report a problem" }).click();
  const dialog = page.getByRole("dialog", { name: "Report a problem" });
  await expect(dialog).toBeVisible();
  return dialog;
}

test.describe("reporting a problem", () => {
  test("goes from the help menu to a zip that opens, and GitHub's form filled in", async ({ page, consoleServer }) => {
    test.setTimeout(120_000);
    await page.setViewportSize({ width: 1440, height: 900 });
    await stubGithub(page);
    await signIn(page, consoleServer, "/databases");
    const dialog = await openFromHelp(page);

    // Step 1: what happened. The area comes from the page.
    await expect(dialog.getByRole("combobox", { name: "Where does it happen" })).toHaveText(/Databases/);
    await settle(page);
    await expectNoA11yViolations(page, "report, step 1");
    await dialog.getByRole("textbox", { name: "Title" }).fill("The table stays grey when I open Data");
    await dialog.getByRole("textbox", { name: "What happened" }).fill("Opening Data the table never loads.\nThe console of the browser shows an error.");
    await dialog.getByRole("textbox", { name: "Steps to make it happen again" }).fill("1. Open Databases.\n2. Press Data.");
    await dialog.locator('input[type="file"]').setInputFiles({ name: "captura.png", mimeType: "image/png", buffer: PNG });
    await expect(dialog.getByText("captura.png")).toBeVisible();
    await dialog.getByRole("button", { name: "Next: what is sent" }).click();

    // Step 2: the one file, each part as it is written. The support report is the real one, made now.
    const parts = dialog.getByRole("list", { name: "Parts of the file" });
    await expect(parts.getByRole("button", { name: /support-report\.md/ })).toBeVisible({ timeout: 30_000 });
    await expect(parts.getByRole("button", { name: /browser\.json/ })).toBeVisible();
    await expect(parts.getByRole("button", { name: /images\/captura\.png/ })).toBeVisible();
    await parts.getByRole("button", { name: /support-report\.md/ }).click();
    await expect(dialog.getByRole("region", { name: "support-report.md" })).toContainText("Noust support report");
    await parts.getByRole("button", { name: /browser\.json/ }).click();
    await expect(dialog.getByRole("region", { name: "browser.json" })).toContainText('"page": "/databases"');
    await settle(page);
    await expectNoA11yViolations(page, "report, step 2");
    await dialog.getByRole("button", { name: "Next: on GitHub" }).click();

    // Step 3: what will happen and what will already be written there.
    await expect(dialog.getByText("A file is downloaded")).toBeVisible();
    await expect(dialog.getByText("[Console] The table stays grey when I open Data")).toBeVisible();
    await expect(dialog.getByText("Databases", { exact: true })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "report, step 3");

    // One press: the file is downloaded and GitHub's form is opened.
    const [download, github] = await Promise.all([
      page.waitForEvent("download"),
      page.context().waitForEvent("page"),
      dialog.getByRole("button", { name: "Download and open GitHub" }).click(),
    ]);
    expect(download.suggestedFilename()).toMatch(/^noust-report-\d{8}-\d{4}\.zip$/);
    const saved = path.join(mkdtempSync(path.join(tmpdir(), "noust-report-")), download.suggestedFilename());
    await download.saveAs(saved);

    // A stock unzipper accepts it (CRC of every part included) and finds the three parts.
    execFileSync(python(), ["-m", "zipfile", "-t", saved]);
    const listing = execFileSync(python(), ["-m", "zipfile", "-l", saved]).toString();
    expect(listing).toContain("support-report.md");
    expect(listing).toContain("browser.json");
    expect(listing).toContain("images/captura.png");

    const url = new URL(github.url());
    expect(`${url.origin}${url.pathname}`).toBe("https://github.com/Perkybeet/noust/issues/new");
    expect(url.searchParams.get("template")).toBe("console_report.yml");
    expect(url.searchParams.get("title")).toBe("[Console] The table stays grey when I open Data");
    expect(url.searchParams.get("area")).toBe("Databases");
    expect(url.searchParams.get("what-happened")).toContain("Opening Data the table never loads.");
    expect(url.searchParams.get("reproduce")).toBe("1. Open Databases.\n2. Press Data.");
    expect(url.searchParams.get("version")).not.toBeNull();
    await github.close();

    // The end: what was done, and what is left to do on GitHub.
    await expect(dialog.getByText("GitHub was opened in another tab, with the form filled in")).toBeVisible();
    await expect(dialog.getByText(/Nothing is published until then/)).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "report, the end");
    await dialog.getByRole("button", { name: "Close", exact: true }).first().click();
    await expect(dialog).toBeHidden();
  });

  test("hides what looks like a secret in an error of the page, and marks it as hidden", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, "/apps");
    // An error of the page that carries a credential, as one of the console's own logs could.
    await page.evaluate(() => {
      window.dispatchEvent(new ErrorEvent("error", { message: "Request to postgres://app:hunter22hunter@db.internal:5432/shop failed", error: new Error("Request to postgres://app:hunter22hunter@db.internal:5432/shop failed") }));
    });
    const dialog = await openFromHelp(page);
    await dialog.getByRole("textbox", { name: "Title" }).fill("It failed");
    await dialog.getByRole("textbox", { name: "What happened" }).fill("It failed.");
    await dialog.getByRole("button", { name: "Next: what is sent" }).click();
    const parts = dialog.getByRole("list", { name: "Parts of the file" });
    await parts.getByRole("button", { name: /browser\.json/ }).click();

    const block = dialog.getByRole("region", { name: "browser.json" });
    await expect(block).toContainText("postgres://app:");
    await expect(block.locator("mark")).not.toHaveCount(0);
    await expect(block).not.toContainText("hunter22hunter");
  });

  test("on a phone it opens from More, and fits the screen", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await signIn(page, consoleServer, "/databases");
    await page.getByRole("button", { name: "More", exact: true }).click();
    // The sheet is opened and still before it is pressed: it slides in, and a press on a piece that moves can miss it.
    const sheet = page.getByRole("dialog", { name: "All areas" });
    await expect(sheet).toBeVisible();
    await settle(page);
    await sheet.getByRole("button", { name: "Report a problem" }).click();
    const dialog = page.getByRole("dialog", { name: "Report a problem" });
    await expect(dialog).toBeVisible();
    await settle(page);

    const box = await dialog.boundingBox();
    expect(box?.width ?? 0).toBeLessThanOrEqual(390);
    expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
    await expectNoA11yViolations(page, "report on a phone");
  });

  test("keeps what was written when the person goes back to a step that is done", async ({ page, consoleServer }) => {
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, "/domains");
    const dialog = await openFromHelp(page);
    await expect(dialog.getByRole("combobox", { name: "Where does it happen" })).toHaveText(/Domains/);
    await dialog.getByRole("textbox", { name: "Title" }).fill("Kept");
    await dialog.getByRole("textbox", { name: "What happened" }).fill("Kept too.");
    await dialog.getByRole("button", { name: "Next: what is sent" }).click();
    await expect(dialog.getByRole("list", { name: "Parts of the file" })).toBeVisible();
    await dialog.getByRole("button", { name: "What happened: done" }).click();

    await expect(dialog.getByRole("textbox", { name: "Title" })).toHaveValue("Kept");
    await expect(dialog.getByRole("textbox", { name: "What happened" })).toHaveValue("Kept too.");
  });
});

test.describe("reporting a problem from the screen of a page that broke", () => {
  test("opens with the error already inside, and the area of the page", async ({ page, consoleServer, problems }) => {
    // React logs the error it caught, and the page's own load answers what the test sends it.
    problems.expect(/A page failed to render|Cannot read|apps/);
    await page.setViewportSize({ width: 1440, height: 900 });
    await signIn(page, consoleServer, "/apps");
    // The list answers something that is not a list: the page cannot be drawn.
    await page.route("**/api/apps*", (route) => route.fulfill({ status: 200, contentType: "application/json", body: JSON.stringify({ total: 1, apps: [{ domain: 5, name: null }] }) }));
    await page.goto("/apps");

    const broken = page.getByRole("heading", { level: 1, name: "This page broke" });
    await expect(broken).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "the screen of a page that broke");
    await page.getByRole("button", { name: "Report a problem" }).click();
    const dialog = page.getByRole("dialog", { name: "Report a problem" });
    await expect(dialog).toBeVisible();

    const happened = dialog.getByRole("textbox", { name: "What happened" });
    await expect(happened).toHaveValue(/^The page broke with this error:\n.+/);
    await expect(dialog.getByRole("combobox", { name: "Where does it happen" })).toHaveText(/Applications and deploys/);
    await settle(page);
    await expectNoA11yViolations(page, "report from the error screen");
  });
});
