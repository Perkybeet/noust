/**
 * The system's logs, a source of Logs, searched with a query against the real backend (checklist
 * M-40): fields and operators judged by the server, the query shown as it was understood, a query
 * it cannot read answered in its own words, an entry opened in context with its neighbours (and a
 * link that names one), "Live" among the ranges, and the same stretch carried to and from "what
 * happened here". The Server area's old address lands here.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const SEARCH = "Search the logs";
const SYSTEM = "/logs?source=system";

test("a query with fields narrows the journal, says how it was understood, and opens an entry in context", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, SYSTEM);
  const viewer = page.getByRole("listbox", { name: "System logs" });
  await expect(viewer.getByRole("option", { name: /Console listening on 127\.0\.0\.1:8080/ })).toBeVisible();

  await page.getByRole("searchbox", { name: SEARCH }).fill("@unit:nginx @level:err connect -timeout");
  await expect(page.getByText("Understood as")).toBeVisible();
  await expect(page.getByText("@unit:nginx AND @level:err AND connect AND NOT timeout")).toBeVisible();
  await expect(viewer.getByRole("option")).toHaveCount(1);
  const refused = viewer.getByRole("option", { name: /connect\(\) failed \(111: Connection refused\)/ });
  await expect(refused).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the logs, searched with a query");

  // The entry opens in context: what was logged around it, from every service, and then its own.
  await refused.click();
  const drawer = page.getByRole("dialog", { name: "Around this entry" });
  await expect(drawer.getByText(/Received disconnect from 203\.0\.113\.7/)).toBeVisible();
  await expect(drawer.getByText(/Unable to lock \.\/ibdata1/)).toBeVisible();
  await expect(drawer.locator("[aria-current='true']")).toContainText("connect() failed");
  // The line is in the address: the link to this view opens the same entry.
  await expect(page).toHaveURL(/line=/);
  await settle(page);
  await expectNoA11yViolations(page, "an entry in context");

  await drawer.getByRole("checkbox", { name: "Only nginx.service" }).click();
  await expect(drawer.getByText(/Received disconnect/)).toBeHidden();
  await expect(drawer.locator("[aria-current='true']")).toContainText("connect() failed");

  // Opened again from the address alone, which names only the entry.
  const link = page.url();
  await page.goto(link);
  const again = page.getByRole("dialog", { name: "Around this entry" });
  await expect(again.locator("[aria-current='true']")).toContainText("connect() failed");
});

test("a query the server cannot read is answered in the server's words, with the syntax at hand", async ({ page, consoleServer, problems }) => {
  problems.expect(/status of 400 .* \/api\/server\/logs\/search$/);
  await signIn(page, consoleServer, `${SYSTEM}&unit=nginx.service`);
  const viewer = page.getByRole("listbox", { name: "System logs" });
  await expect(viewer.getByRole("option").first()).toBeVisible();

  await page.getByRole("searchbox", { name: SEARCH }).fill("(nginx");
  await expect(page.getByText(/A parenthesis is not closed/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a query that cannot be read");

  await page.getByRole("button", { name: "Query syntax" }).click();
  await expect(page.getByRole("dialog").getByText("@unit:nginx", { exact: true })).toBeVisible();
  await expect(page.getByRole("dialog").getByText("@app:shop.example.com", { exact: true })).toBeVisible();
  await expectNoA11yViolations(page, "the query syntax");
});

test("Live is a range of the system's logs, and the viewer's Follow is the only other control that follows", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, SYSTEM);
  await expect(page.getByRole("listbox", { name: "System logs" }).getByRole("option").first()).toBeVisible();
  await expect(page.getByRole("switch", { name: "Live" })).toHaveCount(0);
  await expect(page.getByRole("button", { name: "Follow" })).toHaveCount(1);

  await page.getByRole("combobox", { name: "How far back" }).click();
  await page.getByRole("option", { name: "Live" }).click();
  await expect(page).toHaveURL(/since=live/);
  await expect(page.getByRole("combobox", { name: "How far back" })).toHaveText("Live");
  await expect(page.getByRole("button", { name: "Follow" })).toHaveAttribute("aria-pressed", "true");
  await expect(page.getByText("noust server logs --since -15min")).toBeVisible();
});

test("the old address of the Server's logs lands on the system source, with its filters", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server/logs?unit=nginx.service&priority=err&range=24h");
  await expect(page).toHaveURL(/\/logs\?/);
  await expect(page).toHaveURL(/source=system/);
  await expect(page).toHaveURL(/unit=nginx\.service/);
  await expect(page).toHaveURL(/level=err/);
  await expect(page).toHaveURL(/since=24h/);
  await expect(page.getByRole("radio", { name: "System" })).toBeChecked();
});

test("a stretch of the timeline is searched with the query language, and the logs open what happened in theirs", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, SYSTEM);
  await page.getByRole("button", { name: "What happened here" }).click();
  const timeline = page.getByRole("dialog", { name: "What happened in this stretch" });
  await expect(timeline).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "what happened here, from the logs");

  await timeline.getByRole("link", { name: "Search these logs" }).click();
  await expect(page).toHaveURL(/\/logs\?.*from=\d+.*to=\d+/);
  await expect(page).toHaveURL(/source=system/);
  await expect(page.getByText(/^Showing .* to .*\.$/)).toBeVisible();
  await page.getByRole("button", { name: "Back to the last hour" }).click();
  await expect(page).not.toHaveURL(/from=/);
  await expect(page.getByRole("combobox", { name: "How far back" })).toHaveText("1 hour");
});
