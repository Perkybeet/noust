/**
 * The terminal and the one-off command against the real backend: a ticket minted inside sudo
 * mode, a WebSocket that redeems it, the console's own emulator drawing on a canvas, and the
 * job a command runs as. The shell is the fake runner's, which echoes what is typed and ends
 * on `exit` (console_server.py), so nothing runs on the machine the suite runs on.
 *
 * The screen is a canvas, which a test cannot read: it is read through the text view a screen
 * reader gets, and its pixels are checked to prove something was drawn. Both themes, with the
 * CSP and console gates of the `problems` fixture: a canvas that needed an inline style would
 * fail here and nowhere else.
 */

import type { Locator, Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const APP = "tienda.example.org";

function terminal(page: Page): Locator {
  return page.getByRole("textbox", { name: `Terminal - ${APP}` });
}

function textView(page: Page): Locator {
  return page.getByRole("region", { name: "Terminal contents as text" });
}

/** How many distinct colours the canvas holds: a blank one holds one. */
async function coloursDrawn(canvas: Locator): Promise<number> {
  return canvas.evaluate((element) => {
    if (!(element instanceof HTMLCanvasElement)) return 0;
    const context = element.getContext("2d");
    if (context === null) return 0;
    const { data } = context.getImageData(0, 0, element.width, element.height);
    const seen = new Set<number>();
    for (let index = 0; index + 2 < data.length; index += 4) {
      seen.add(((data[index] ?? 0) << 16) | ((data[index + 1] ?? 0) << 8) | (data[index + 2] ?? 0));
    }
    return seen.size;
  });
}

/** The refusal sudo mode answers first, which Chromium logs as a failed resource. */
const ELEVATION = {
  app: new RegExp(`status of 403 .*/api/apps/${APP.replaceAll(".", "\\.")}/terminal$`),
  exec: new RegExp(`status of 403 .*/api/apps/${APP.replaceAll(".", "\\.")}/exec$`),
  server: /status of 403 .*\/api\/server\/terminal$/,
};

/** The text view is a checkbox in the Display menu, which closes again so the terminal has the keyboard. */
async function toggleTextView(page: Page): Promise<void> {
  await page.getByRole("button", { name: "Display" }).click();
  await page.getByRole("checkbox", { name: /Text view/ }).click();
  await page.keyboard.press("Escape");
}

async function openTerminal(page: Page, consoleServer: Parameters<typeof confirmItsYou>[1]): Promise<void> {
  await page.getByRole("button", { name: "Open terminal" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
}

test("a terminal opens as the application, draws a shell and ends when the shell does", async ({ page, consoleServer, context, problems }) => {
  problems.expect(ELEVATION.app);
  await context.grantPermissions(["clipboard-read", "clipboard-write"]);
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, `/apps/${APP}/terminal`);

  // Before it opens: what it is, what is kept of it, and one button.
  await expect(page.getByRole("heading", { name: `A shell as ${APP}` })).toBeVisible();
  await expect(page.getByText("As the account the application runs as, in its sandbox.")).toBeVisible();
  // The rest of what it is stays behind one button, which says what is kept of it.
  await page.getByRole("button", { name: "How this session works" }).click();
  await expect(page.getByText(/Everything you type and everything it prints is kept in the audit trail/)).toBeVisible();
  await page.keyboard.press("Escape");
  await settle(page);
  await expectNoA11yViolations(page, "the terminal before it opens");

  // Opening it asks for sudo mode, then connects.
  await openTerminal(page, consoleServer);
  const header = page.getByRole("group", { name: "Terminal session" });
  await expect(header).toContainText(APP);
  await expect(header).toContainText(/Ends in \d+:\d\d:\d\d/);
  await expect(header).toContainText("Closes after 10 min without typing");
  await expect(header).toContainText("Recorded in the audit trail");

  // The keyboard is the terminal's without a click, and the screen is drawn on a canvas.
  await expect(terminal(page)).toBeFocused();
  await toggleTextView(page);
  await expect(textView(page)).toContainText("Fake terminal: nothing here runs on this machine.");
  await toggleTextView(page);
  await expect(textView(page)).toBeHidden();
  await expect.poll(() => coloursDrawn(page.locator("canvas").first())).toBeGreaterThan(1);

  // What is typed reaches the shell, and what it prints comes back.
  await terminal(page).focus();
  await page.keyboard.type("echo hello from the shell");
  await page.keyboard.press("Enter");
  await page.keyboard.type("whoami");
  await page.keyboard.press("Enter");
  await toggleTextView(page);
  await expect(textView(page)).toContainText("hello from the shell");
  // The account the header names is the one the shell says it is.
  const account = (await header.locator("[translate='no']").nth(1).innerText()).trim();
  expect(account).not.toBe("");
  await expect(textView(page)).toContainText(account);

  // Copy takes what is on the screen, with the shortcut that is not an interrupt.
  await terminal(page).focus();
  await page.keyboard.press("Control+Shift+C");
  await expect(page.getByText("Copied to clipboard")).toBeVisible();
  expect(await page.evaluate(() => navigator.clipboard.readText())).toContain("hello from the shell");

  await settle(page);
  await expectNoA11yViolations(page, "a terminal with a session open");

  // The shell ends: said in words, and a new one is one press away.
  await terminal(page).focus();
  await page.keyboard.type("exit");
  await page.keyboard.press("Enter");
  await expect(page.getByText("The shell exited.")).toBeVisible();
  await expect(page.getByText("Ended", { exact: true })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a terminal after its shell ended");

  await page.getByRole("button", { name: "Open a new terminal" }).click();
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "End session" }).click();
  await expect(page.getByText("The session was ended.")).toBeVisible();
});

test("the grid follows the window, and the keyboard can leave the terminal", async ({ page, consoleServer, problems }) => {
  problems.expect(ELEVATION.app);
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, `/apps/${APP}/terminal`);
  await expect(page.getByRole("heading", { name: `A shell as ${APP}` })).toBeVisible();
  await openTerminal(page, consoleServer);

  const canvas = page.locator("canvas").first();
  const wide = await canvas.evaluate((element) => (element instanceof HTMLCanvasElement ? element.width : 0));
  await page.setViewportSize({ width: 900, height: 900 });
  await expect.poll(() => canvas.evaluate((element) => (element instanceof HTMLCanvasElement ? element.width : 0))).toBeLessThan(wide);

  // Tab belongs to the shell, so Escape and then Tab is the way out for a keyboard.
  await terminal(page).focus();
  await page.keyboard.press("Escape");
  await page.keyboard.press("Tab");
  await expect(terminal(page)).not.toBeFocused();
});

test("it is reached from the application's More actions, and from the server's", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, `/apps/${APP}`);
  await expect(page.getByRole("heading", { level: 1, name: APP })).toBeVisible();
  await page.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "Terminal" }).click();
  await expect(page).toHaveURL(new RegExp(`/apps/${APP.replaceAll(".", "\\.")}/terminal$`));
  await expect(page.getByRole("heading", { name: `A shell as ${APP}` })).toBeVisible();

  await page.goto("/server");
  await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
  await page.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "Open a terminal" }).click();
  await expect(page).toHaveURL(/\/server\/terminal$/);
});

test("a command runs once, as a job, and its output is shown as it was printed", async ({ page, consoleServer, problems }) => {
  problems.expect(ELEVATION.exec);
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, `/apps/${APP}/terminal`);
  await expect(page.getByRole("heading", { name: "Run a command once" })).toBeVisible();

  // The line is split the way it will run, and nothing in it is read by a shell.
  const command = page.getByRole("textbox", { name: /Command/ });
  await command.fill("echo hello 'two words' | wc");
  const argv = page.getByRole("list", { name: "Run as" });
  await expect(argv.getByRole("listitem")).toHaveText(["echo", "hello", "two words", "|", "wc"]);
  await expect(page.getByText("These are passed as text, not interpreted: |")).toBeVisible();
  await expect(page.getByText("noust app exec tienda.example.org -- echo hello 'two words' '|' wc")).toBeVisible();

  await command.fill("echo hello world");
  await page.getByRole("button", { name: "Run command" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(page.getByText("The command finished")).toBeVisible();
  await expect(page.getByText("Exit status 0.")).toBeVisible();
  const output = page.getByRole("region", { name: `Log of the command in ${APP}` });
  await expect(output).toContainText("hello world");
  // The queue's own stamp and level are not the program's output.
  await expect(output).not.toContainText("[INFO]");
  await settle(page);
  await expectNoA11yViolations(page, "a command that finished");

  // A command that fails says so in the system's words, and the page goes on.
  await command.fill("false");
  await page.getByRole("button", { name: "Run another" }).click();
  await expect(page.getByText(`The command in ${APP} failed`)).toBeVisible();
  // Said in the failure, in the job's log and in the notice that a job failed: any of them.
  await expect(page.getByText("The command exited with status 1").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a command that failed");
});

test("the server's own terminal is a root shell, and says so", async ({ page, consoleServer, problems }) => {
  problems.expect(ELEVATION.server);
  await page.setViewportSize({ width: 1440, height: 900 });
  await signIn(page, consoleServer, "/server/terminal");
  await expect(page.getByRole("heading", { name: "A root shell on the server" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the server's terminal before it opens");

  await page.getByRole("button", { name: "Open terminal" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(page.getByText("Connected", { exact: true })).toBeVisible();
  await expect(page.getByRole("group", { name: "Terminal session" })).toContainText("root");
  await toggleTextView(page);
  await expect(page.getByRole("region", { name: "Terminal contents as text" })).toContainText("Fake terminal");
});

test("a narrow screen keeps the terminal usable", async ({ page, consoleServer, problems }) => {
  problems.expect(ELEVATION.app);
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, consoleServer, `/apps/${APP}/terminal`);
  await expect(page.getByRole("heading", { name: `A shell as ${APP}` })).toBeVisible();
  await openTerminal(page, consoleServer);
  await expect(page.getByRole("button", { name: "End session" })).toBeVisible();
  // Nothing overflows sideways.
  expect(await page.evaluate(() => document.documentElement.scrollWidth <= window.innerWidth)).toBe(true);
  await settle(page);
  await expectNoA11yViolations(page, "a terminal on a phone");
});
