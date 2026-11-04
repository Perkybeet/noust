/**
 * Home against the real backend and its seeded machine: a panel of widgets that reads what the console reads
 * (what needs attention, in the server's own words; the six figures; the applications; the machine), arranged by
 * the person with the pointer, with the keyboard and with buttons, kept across a reload, and put back as it came.
 * Both themes; every test fails on a Content Security Policy violation or a console error through the `problems`
 * fixture, which is the proof that dragging, resizing and the gallery inject nothing the policy forbids.
 *
 * The suite signs in with the access token, which is no account: the server answers the panels 400 and the
 * browser keeps them, so a reload here is what an account's second visit is.
 */

import type { Locator, Page } from "@playwright/test";

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

/** The app the seed fails: its newest deploy failed with npm's own words. */
const FAILED = "clientes.example.com";

/** A widget, found by the name in its head. */
function widget(page: Page, name: string): Locator {
  return page.locator("article").filter({ has: page.getByRole("heading", { level: 2, name, exact: true }) });
}

/** The names of the widgets on the page, in the order they are drawn. */
async function names(page: Page): Promise<string[]> {
  return page.locator("main [data-widget-grid] h2").allTextContents();
}

async function centre(locator: Locator): Promise<{ x: number; y: number }> {
  const box = await locator.boundingBox();
  if (box === null) throw new Error("not on screen");
  return { x: box.x + box.width / 2, y: box.y + box.height / 2 };
}

/** Takes hold of something with the pointer and carries it, in steps, so the page sees a drag and not a jump. */
async function drag(page: Page, from: { x: number; y: number }, to: { x: number; y: number }): Promise<void> {
  await page.mouse.move(from.x, from.y);
  await page.mouse.down();
  await page.mouse.move(from.x + 6, from.y + 6, { steps: 3 });
  await page.mouse.move(to.x, to.y, { steps: 18 });
  await page.mouse.up();
}

async function customize(page: Page): Promise<void> {
  await page.getByRole("link", { name: "Customize" }).click();
  await expect(page.getByRole("heading", { level: 1, name: "Customize “This server”" })).toBeVisible();
}

/** The gallery is the second column of the frame: one card has a name and a "+". */
function gallery(page: Page): Locator {
  return page.locator('[data-frame="column"]');
}

test.beforeEach(async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer);
  await expect(page.getByRole("heading", { level: 1, name: "This server" })).toBeVisible();
  await expect(widget(page, "Needs attention")).toBeVisible();
});

test("draws the panel that comes with the console, the failing app first and in its own words", async ({ page }) => {
  expect(await names(page)).toEqual([
    "Needs attention",
    "Server status",
    "Resources",
    "Work in progress",
    "Certificates",
    "Backups",
    "Applications",
    "Quick actions",
    "Recent deployments",
    "Recent activity",
  ]);
  const attention = widget(page, "Needs attention");
  const item = attention.getByRole("listitem").filter({ hasText: FAILED }).first();
  await expect(item).toBeVisible();
  await expect(item.getByText("Last deploy failed")).toBeVisible();
  // Verbatim, not paraphrased: what the deploy recorded, after the sentence that says what it means.
  await expect(item.getByText("npm ERR! code ELIFECYCLE", { exact: false }).first()).toBeVisible();
  await expect(item.getByRole("link", { name: `View log of the deploy of ${FAILED}` })).toHaveAttribute("href", new RegExp(`^/apps/${FAILED.replace(/\./g, "\\.")}/deployments/\\d+$`));
  // The failed unit that is no app's, and the six figures, are there too.
  await expect(attention.getByText("queue-worker.service").or(attention.getByText("queue-worker")).first()).toBeVisible();
  await expect(widget(page, "Server status").getByRole("link", { name: /Applications/ })).toHaveAttribute("href", "/apps");
  // One primary for the page, the header's.
  await expect(page.locator("[data-slot=header]").getByRole("link", { name: "New application" })).toHaveCount(1);
  await settle(page);
  await expectNoA11yViolations(page, "home");
});

test("keeps the widgets in the room reserved for them: nothing moves when their data arrives", async ({ page }) => {
  const first = await widget(page, "Needs attention").boundingBox();
  const last = await widget(page, "Recent activity").boundingBox();
  await settle(page);
  expect(await widget(page, "Needs attention").boundingBox()).toEqual(first);
  expect(await widget(page, "Recent activity").boundingBox()).toEqual(last);
});

test.describe("customizing with the pointer", () => {
  test("adds a widget by dragging it from the gallery onto the panel, moves it, resizes it by its corner and takes one away", async ({ page }) => {
    await customize(page);
    await expect(gallery(page).getByText("Add widgets")).toBeVisible();
    await gallery(page).getByRole("searchbox", { name: "Search widgets" }).fill("disk");
    const card = gallery(page).locator("li").filter({ hasText: "How full it is and when it will be" });
    await expect(card).toBeVisible();

    // Brought in by hand: the panel makes room under the pointer while it is over it.
    const grid = page.locator("main [data-widget-grid]");
    const box = await grid.boundingBox();
    if (box === null) throw new Error("no grid");
    await drag(page, await centre(card), { x: box.x + 60, y: box.y + 20 });
    await expect(widget(page, "Disk")).toBeVisible();
    expect((await names(page))[0]).toBe("Disk");
    await expect(page.locator("html")).not.toHaveClass(/widget-dragging/);

    // Moved: its handle is taken to the end of the page's first row of widgets.
    const handle = page.getByRole("button", { name: "Move Disk", exact: true });
    const resources = await widget(page, "Resources").boundingBox();
    if (resources === null) throw new Error("no widget");
    await drag(page, await centre(handle), { x: resources.x + resources.width - 8, y: resources.y + resources.height / 2 });
    // Dropped, it flies to its place, and it is in the order again when it has arrived.
    await expect
      .poll(async () => {
        const order = await names(page);
        return order.indexOf("Disk") > order.indexOf("Resources");
      })
      .toBe(true);

    // Resized by its corner to the nearest size it takes: a few columns wider, and it is a medium one.
    const size = page.getByRole("button", { name: /^Size and position of Disk: / });
    await expect(size).toHaveAccessibleName(/: Small$/);
    const corner = page.locator('[data-cell="disk"] [data-resize]');
    const at = await centre(corner);
    await drag(page, at, { x: at.x + 260, y: at.y });
    await expect(size).toHaveAccessibleName(/: (Medium|Wide)$/);

    // Taken away with its cross, and the keyboard goes to a neighbour, never to nowhere.
    await page.getByRole("button", { name: "Remove Disk" }).click();
    await expect(widget(page, "Disk")).toHaveCount(0);
    await expect(page.locator("main [data-widget-grid] :focus")).toHaveCount(1);
  });

  test("keeps what was arranged across a reload, and puts the panel back as it came", async ({ page }) => {
    await customize(page);
    await page.getByRole("button", { name: "Remove Resources" }).click();
    await expect(widget(page, "Resources")).toHaveCount(0);
    await page.getByRole("link", { name: "Done" }).click();
    await expect(page.getByRole("link", { name: "Customize" })).toBeVisible();
    const arranged = await names(page);
    expect(arranged).not.toContain("Resources");
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "This server" })).toBeVisible();
    await expect(widget(page, "Needs attention")).toBeVisible();
    expect(await names(page)).toEqual(arranged);

    await customize(page);
    await page.getByRole("button", { name: "Reset" }).click();
    await expect(widget(page, "Resources")).toBeVisible();
    await page.getByRole("link", { name: "Done" }).click();
    await page.reload();
    await expect(widget(page, "Needs attention")).toBeVisible();
    expect(await names(page)).toContain("Resources");
  });

  test("shows the arranged page with no accessibility violation", async ({ page }) => {
    await customize(page);
    await expect(page.getByRole("button", { name: "Move Needs attention", exact: true })).toBeVisible();
    await settle(page);
    await expectNoA11yViolations(page, "home, customized");
  });
});

test.describe("customizing without a pointer", () => {
  test("lifts a widget with Space, moves it with the arrows and drops it, and says each step", async ({ page }) => {
    await customize(page);
    const before = await names(page);
    const handle = page.getByRole("button", { name: "Move Resources", exact: true });
    await handle.focus();
    await page.keyboard.press("Space");
    await expect(handle).toHaveAttribute("aria-pressed", "true");
    await expect(page.getByTestId("announcer-polite")).toContainText("Resources lifted");
    await page.keyboard.press("ArrowRight");
    await expect(page.getByTestId("announcer-polite")).toContainText("Resources: position 4 of 10");
    await expect(handle).toBeFocused();
    await page.keyboard.press("Space");
    await expect(handle).toHaveAttribute("aria-pressed", "false");
    const after = await names(page);
    expect(after.indexOf("Resources")).toBe(before.indexOf("Resources") + 1);

    // Escape puts a lifted widget back where it was.
    await page.keyboard.press("Space");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Escape");
    expect(await names(page)).toEqual(after);
  });

  test("changes a size and a place from the menu of the widget's size, and adds from the gallery with its +", async ({ page }) => {
    await customize(page);
    await page.getByRole("button", { name: /^Size and position of Resources: / }).click();
    await page.getByRole("menuitem", { name: /^Medium/ }).click();
    await expect(page.getByRole("button", { name: "Size and position of Resources: Medium" })).toBeVisible();
    await page.getByRole("button", { name: "Size and position of Resources: Medium" }).click();
    await page.getByRole("menuitem", { name: "Move later" }).click();
    expect((await names(page)).indexOf("Resources")).toBe(3);

    await gallery(page).getByRole("searchbox", { name: "Search widgets" }).fill("processes");
    await gallery(page).getByRole("button", { name: "Add Busiest processes" }).focus();
    await page.keyboard.press("Enter");
    await expect(widget(page, "Busiest processes")).toBeVisible();
    expect((await names(page)).at(-1)).toBe("Busiest processes");
  });

  test("moves a widget at once, with nothing sliding, when the person asked for less motion", async ({ page }) => {
    await page.emulateMedia({ reducedMotion: "reduce" });
    await customize(page);
    const handle = page.getByRole("button", { name: "Move Resources", exact: true });
    await handle.focus();
    await page.keyboard.press("Space");
    await page.keyboard.press("ArrowRight");
    await page.keyboard.press("Space");
    const moving = await page.locator("main [data-cell]").evaluateAll((cells) => cells.filter((cell) => (cell as HTMLElement).style.transform !== "").length);
    expect(moving).toBe(0);
  });
});

test("a new panel is made from a name and what it follows, and opens customized", async ({ page }) => {
  await page.goto("/?panel=new");
  await expect(page.getByRole("heading", { level: 1, name: "New panel" })).toBeVisible();
  await page.getByRole("textbox", { name: "Name" }).fill("Data and backups");
  await page.getByRole("radio", { name: "Empty" }).click();
  await page.getByRole("button", { name: "Create and customize" }).click();
  await expect(page).toHaveURL(/\/\?panel=data-and-backups&customise=true$/);
  await expect(page.getByRole("heading", { level: 1, name: "Customize “Data and backups”" })).toBeVisible();
  await expect(page.getByText("The panel is empty: add widgets from the gallery.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a new panel");
});

test.describe("on a phone", () => {
  test.beforeEach(async ({ page }) => {
    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto("/");
    await expect(page.getByRole("heading", { level: 1, name: "This server" })).toBeVisible();
    await expect(widget(page, "Needs attention")).toBeVisible();
  });

  test("never scrolls sideways, and what needs attention comes first", async ({ page }) => {
    await settle(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await expectNoA11yViolations(page, "home on a phone");
  });

  test("adds a widget from a sheet and keeps it", async ({ page }) => {
    await page.goto("/?customise=true");
    await expect(page.getByRole("heading", { level: 1, name: "Customize “This server”" })).toBeVisible();
    await page.getByRole("button", { name: "Add a widget" }).click();
    const sheet = page.getByRole("dialog", { name: "Add widgets" });
    await expect(sheet).toBeVisible();
    await sheet.getByRole("searchbox", { name: "Search widgets" }).fill("disk");
    await sheet.getByRole("button", { name: "Add Disk" }).click();
    await expect(sheet).toBeHidden();
    await expect(widget(page, "Disk")).toBeVisible();
    await settle(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow).toBeLessThanOrEqual(0);
    await page.reload();
    await expect(page.getByRole("heading", { level: 1, name: "Customize “This server”" })).toBeVisible();
    await expect(widget(page, "Disk")).toBeVisible();
  });
});
