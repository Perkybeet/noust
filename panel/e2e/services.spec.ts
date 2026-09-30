/**
 * Services against the real backend, as a tab of Server: the list, a unit's own page (T2) and
 * its unit file (T6, behind elevation), its actions, and deleting it (behind More actions,
 * confirmed by typing the name). The 3.0 addresses redirect.
 */

import { expect, expectNoA11yViolations, settle, signIn, test, toasts } from "./fixtures";
import { confirmItsYou } from "./settings.helpers";

test("lists a seeded Noust-managed service in the Server area and opens its page", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/services");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Server");
  await expect(page.getByRole("navigation", { name: "Server sections" }).getByRole("link", { name: "Services" })).toHaveAttribute("aria-current", "page");

  const table = page.getByRole("region", { name: /^Services/ });
  const link = table.getByRole("link", { name: "wasm-shop-example-net" });
  await expect(link).toBeVisible();

  await settle(page);
  await expectNoA11yViolations(page, "the services tab");

  await link.click();
  await expect(page).toHaveURL(/\/server\/services\/wasm-shop-example-net$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("wasm-shop-example-net");
  await expect(page.getByRole("navigation", { name: "Service sections" })).toBeVisible();
  // Its facts say who manages it (a foreign unit's page says Noust did not create it).
  await expect(page.getByText("Managed by", { exact: true })).toBeVisible();
  await expectNoA11yViolations(page, "a service's own page");

  await page.getByRole("navigation", { name: "Service sections" }).getByRole("link", { name: "Logs" }).click();
  await expect(page.getByRole("region", { name: /Logs for/ })).toBeVisible();
  await expectNoA11yViolations(page, "a service's logs");
});

test("the 3.0 addresses of the services redirect under Server", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/services?q=shop");
  await expect(page).toHaveURL(/\/server\/services\?q=shop$/);
  await page.goto("/services/wasm-shop-example-net");
  await expect(page).toHaveURL(/\/server\/services\/wasm-shop-example-net$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("wasm-shop-example-net");
});

test("restarts a unit from its own page", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/services/wasm-shop-example-net");
  const restarted = page.waitForResponse((response) => response.url().endsWith("/api/services/wasm-shop-example-net/restart"));
  await page.getByRole("button", { name: "Restart" }).click();
  expect((await restarted).status()).toBe(200);
  await expect(toasts(page).getByText("Restarted wasm-shop-example-net")).toBeVisible();
});

test("creates a service in simple mode and finds it in the list", async ({ page, consoleServer, problems }) => {
  // Creating a service asks "Confirm it's you" by answering 403 first, by design.
  problems.expect(/status of 403 .* \/api\/services$/);
  await signIn(page, consoleServer, "/server/services");
  await page.getByRole("button", { name: "New service" }).click();
  const dialog = page.getByRole("dialog", { name: "New service" });
  await expectNoA11yViolations(page, "the new service dialog");

  await dialog.getByLabel("Name", { exact: true }).fill("e2e-worker");
  await dialog.getByLabel("Command", { exact: true }).fill("/usr/bin/node worker.js");
  const created = page.waitForResponse(
    (response) => response.url().endsWith("/api/services") && response.request().method() === "POST" && response.status() === 200,
  );
  await dialog.getByRole("button", { name: "Create service" }).click();
  await confirmItsYou(page, consoleServer);
  expect((await created).status()).toBe(200);
  await expect(toasts(page).getByText("Created e2e-worker")).toBeVisible();

  await page.getByRole("searchbox", { name: "Search services" }).fill("e2e-worker");
  await expect(page.getByRole("link", { name: "e2e-worker" })).toBeVisible();
});

test("saving the unit file asks to confirm it's you, then says it was saved", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 403 .*\/api\/services\/wasm-shop-example-net\/config$/);
  await signIn(page, consoleServer, "/server/services/wasm-shop-example-net");
  await page.getByRole("link", { name: "Configuration" }).click();
  await expect(page).toHaveURL(/\/server\/services\/wasm-shop-example-net\/unit$/);
  const editor = page.getByLabel("Configuration of wasm-shop-example-net", { exact: true });
  await expect(editor).toBeVisible();
  await expectNoA11yViolations(page, "the unit file page");
  const original = await editor.inputValue();
  await editor.fill(`${original}\n# edited by e2e\n`);
  await expect(page.getByText(/unsaved change/)).toBeVisible();
  await page.getByRole("button", { name: "Test and save" }).click();

  const confirm = page.getByRole("dialog", { name: "Confirm it's you" });
  await expect(confirm).toBeVisible();
  await page.getByLabel("Authentication code").fill(consoleServer.secondFactor());
  await page.getByRole("button", { name: "Confirm" }).click();
  await expect(confirm).toBeHidden();

  await expect(page.getByText("Saved.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "Restart now" })).toBeVisible();

  // The elevation covers the next ten minutes; reloading the unit file's own read (no
  // elevation needed for GET) confirms the write actually landed.
  await page.reload();
  await expect(page.getByLabel("Configuration of wasm-shop-example-net", { exact: true })).toHaveValue(/# edited by e2e/);
});

test("checks the unit with systemd-analyze before saving, and blocks a save it rejects", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/services/wasm-shop-example-net/unit");
  const editor = page.getByLabel("Configuration of wasm-shop-example-net", { exact: true });
  const original = await editor.inputValue();
  // The fake systemd-analyze refuses a unit with no ExecStart=; nothing else about the file
  // needs to be realistic for the check to fail.
  const withoutExecStart = original
    .split("\n")
    .filter((line) => !line.startsWith("ExecStart="))
    .join("\n");
  await editor.fill(withoutExecStart);

  const verified = page.waitForResponse((response) => response.url().endsWith("/api/services/verify"));
  await page.getByRole("button", { name: "Test", exact: true }).click();
  expect((await verified).status()).toBe(200);

  await expect(page.getByText(/systemd-analyze rejected the unit/)).toBeVisible();
  await expect(page.getByText(/Service has no ExecStart= setting\. Refusing\./)).toBeVisible();
  await expect(page.getByRole("dialog", { name: "Confirm it's you" })).toHaveCount(0);
  await expectNoA11yViolations(page, "the unit file after a failed test");

  // Nothing was written: reloading shows the unit exactly as it was before the attempt.
  await page.reload();
  await expect(page.getByLabel("Configuration of wasm-shop-example-net", { exact: true })).toHaveValue(original);
});

test("shows every unit of the system, one Noust did not create read only", async ({ page, consoleServer, problems }) => {
  // The per-name read only ever answers what the store tracks: expected, once, while the
  // foreign unit's own page falls back to the all-units listing to tell it apart from a name
  // that does not exist at all (see useServiceRecord).
  problems.expect(/status of 404 .*\/api\/services\/postgresql$/);
  await signIn(page, consoleServer, "/server/services");
  await expect(page.getByRole("link", { name: "postgresql" })).toHaveCount(0);

  await page.getByRole("radio", { name: "Whole system" }).click();
  await expect(page).toHaveURL(/[?&]all=true/);

  const row = page.getByRole("row").filter({ has: page.getByText("postgresql", { exact: true }) });
  await expect(row).toBeVisible();
  // The Managed column on a desktop (a phone says it beside the name instead).
  await expect(row.getByRole("cell", { name: "Foreign", exact: true })).toBeVisible();

  await settle(page);
  await expectNoA11yViolations(page, "the services tab with every unit shown");

  await row.getByRole("link", { name: "postgresql" }).click();
  await expect(page).toHaveURL(/\/server\/services\/postgresql$/);
  await expect(page.getByText("Noust did not create this service")).toBeVisible();
  await expect(page.getByRole("button", { name: "More actions" })).toHaveCount(0);
  await expect(page.getByRole("link", { name: "Configuration" })).toHaveCount(0);
  await expectNoA11yViolations(page, "a foreign unit's own page");
});

test("deleting is behind More actions and confirmed by typing its name", async ({ page, consoleServer, problems }) => {
  // Creating asks "Confirm it's you" by answering 403 first; that confirmation elevates the
  // session for the next 10 minutes, so deleting the same service right after does not ask
  // again - only the type-to-confirm safety net below runs.
  problems.expect(/status of 403 .* \/api\/services$/);
  await signIn(page, consoleServer, "/server/services");
  await page.getByRole("button", { name: "New service" }).click();
  const createDialog = page.getByRole("dialog", { name: "New service" });
  await createDialog.getByLabel("Name", { exact: true }).fill("e2e-deleteme");
  await createDialog.getByLabel("Command", { exact: true }).fill("/usr/bin/node worker.js");
  await createDialog.getByRole("button", { name: "Create service" }).click();
  await confirmItsYou(page, consoleServer);
  await expect(toasts(page).getByText("Created e2e-deleteme")).toBeVisible();

  await page.goto("/server/services/e2e-deleteme");
  await page.getByRole("button", { name: "More actions" }).click();
  await page.getByRole("menuitem", { name: "Delete service" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Delete e2e-deleteme" });
  await expect(dialog).toBeVisible();
  const confirmButton = dialog.getByRole("button", { name: "Delete service" });
  await expect(confirmButton).toBeDisabled();
  await dialog.locator("input").fill("e2e-deleteme");
  await expect(confirmButton).toBeEnabled();
  await confirmButton.click();

  await expect(page).toHaveURL(/\/server\/services$/);
  await expect(toasts(page).getByText("Deleted e2e-deleteme")).toBeVisible();
  await expect(page.getByRole("link", { name: "e2e-deleteme" })).toHaveCount(0);
});
