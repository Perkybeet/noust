/**
 * The Domains area against the real backend: each section has a title of its own (never the area's name
 * again) and a tab, a domain is put on an application from the area in one dialog, the card of the names no
 * site serves is apart from the table, Noust DNS asks for its acceptance before it installs, and a server
 * without cloudflared says what a tunnel needs instead of failing a form (the tunnels are Network's since 4.2). Both themes, with the CSP and
 * console gates of the `problems` fixture and axe on every state.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const SECTIONS = [
  { tab: /^Domains/, path: "/domains", title: "Domains" },
  { tab: /^Sites/, path: "/domains/sites", title: "Sites" },
  { tab: /^Certificates/, path: "/domains/certificates", title: "Certificates" },
  { tab: /^DNS/, path: "/domains/dns", title: "DNS" },
] as const;

test("every section is in the column with a title of its own, and moving between them is one click", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/domains");
  const tabs = page.getByRole("navigation", { name: "Domains sections" });
  for (const section of SECTIONS) {
    await tabs.getByRole("link", { name: section.tab }).click();
    await expect(page).toHaveURL(new RegExp(`${section.path}$`));
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(section.title);
    await expect(tabs.getByRole("link", { name: section.tab })).toHaveAttribute("aria-current", "page");
  }
});

test("the Domains section lists the names with their HTTPS and puts a new one on an application from one dialog", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/domains");
  const table = page.getByRole("region", { name: "Domains", exact: true });
  await expect(table.getByRole("row").nth(1)).toBeVisible();
  // example.com's certificate is the one close to expiry: its name says how long is left.
  await expect(table.getByRole("row", { name: /example\.com/ }).first()).toContainText(/Expires in|Valid for/);
  await settle(page);
  await expectNoA11yViolations(page, "the Domains section");

  await page.getByRole("button", { name: "Add domain" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add a domain" });
  await expect(dialog.getByRole("combobox", { name: "Application" })).toBeVisible();
  // Without the application, pressing Add says what is missing where it is missing.
  await dialog.getByLabel("Domain", { exact: true }).fill("soon.example.net");
  await dialog.getByRole("button", { name: /Add domain|Add anyway/ }).click();
  await expect(dialog.getByText("Choose the application the name is for.").first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the Add domain dialog");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

test("on the Sites section the card of the names no site serves is apart from the table, and after it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/domains/sites");
  const table = page.getByRole("region", { name: "Web server sites" });
  const card = page.getByRole("heading", { level: 2, name: "Names no site serves" });
  await expect(table).toBeVisible();
  await expect(card).toBeVisible();
  const tableBox = await table.boundingBox();
  const cardBox = await card.locator("xpath=ancestor::section[1]").boundingBox();
  if (tableBox === null || cardBox === null) throw new Error("nothing is laid out");
  // After the list, with the page's own gap (16px) between the two blocks: never the one stuck to the other.
  expect(cardBox.y).toBeGreaterThan(tableBox.y + tableBox.height);
  expect(cardBox.y - (tableBox.y + tableBox.height)).toBeGreaterThanOrEqual(12);
});

test("Noust DNS says what it takes on and asks for the acceptance before it installs", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/domains/dns");
  // The DNS section offers it beside the providers.
  await expect(page.getByRole("heading", { level: 2, name: "Noust DNS" })).toBeVisible();
  await page.getByRole("link", { name: "Set up Noust DNS" }).click();
  await expect(page).toHaveURL(/\/domains\/dns\/noust$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Noust DNS");
  await expect(page.getByText(/the whole internet depends on: if it is down, every name it serves is down/)).toBeVisible();
  await expect(page.getByText(/can be used to flood other people with traffic/)).toBeVisible();

  // Install without the acceptance: it says what is missing, puts the focus on it and asks the server for nothing.
  let asked = 0;
  page.on("request", (request) => {
    if (request.url().includes("/api/dns/noust/server/install")) asked += 1;
  });
  await page.getByRole("button", { name: "Install Noust DNS" }).click();
  await expect(page.getByText("Accept what it takes on to install it.")).toBeVisible();
  await expect(page.getByRole("checkbox", { name: "I understand and want to install it" })).toBeFocused();
  expect(asked).toBe(0);
  await settle(page);
  await expectNoA11yViolations(page, "Noust DNS before it is installed");
});

test("a server without cloudflared says what a tunnel needs, and offers to install it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/network/tunnels");
  await expect(page.getByText("No tunnels yet")).toBeVisible();
  await expect(page.getByText("It also needs cloudflared, the connector, which is not installed.")).toBeVisible();
  await expect(page.getByRole("button", { name: "Install cloudflared" })).toBeVisible();
  // The server's English never reaches the page as the error of a form.
  await expect(page.getByText("Install it from Cloudflare's own package repository")).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "the Tunnels section without cloudflared");
});

test("the old addresses still land: certificates by tab, sites by tab", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/domains?tab=certificates");
  await expect(page).toHaveURL(/\/domains\/certificates$/);
  await page.goto("/domains?tab=sites");
  await expect(page).toHaveURL(/\/domains\/sites$/);
});
