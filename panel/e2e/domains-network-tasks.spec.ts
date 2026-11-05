/**
 * The tasks of the Domains and Network areas, walked as an operator walks them against the real backend: add a name
 * (one dialog, from the list of names), open a port (one dialog, from Published ports and from a row of what listens),
 * see who listens on a port and whether it can be reached from outside, see the three firewalls apart, and change the
 * nginx of a domain from the list of names. The sandbox models DNS, nginx's syntax check and `ss`; it has no
 * `ufw allow`, so a rule is judged by the request the console sends, which is the contract. Both themes, with the CSP and
 * console gates of the `problems` fixture and axe on every state.
 */

import { csrf } from "./apps-cleanup";
import { elevate } from "./clicks";
import { expect, expectNoA11yViolations, settle, signIn, stillness, test } from "./fixtures";

test("a name is added from the list of names in one dialog: what it is for, the application, where DNS points, and the certificate", async ({ page, consoleServer }) => {
  const app = "example.net";
  const name = `kiosk.${app}`;
  await signIn(page, consoleServer, "/domains");
  const table = page.getByRole("region", { name: "Domains", exact: true });
  await expect(table.getByRole("row").nth(1)).toBeVisible();
  // The columns say what each name is, where it leads and where DNS sends it, in the words of a person.
  await expect(table.getByRole("columnheader", { name: "Leads to" })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "HTTPS" })).toBeVisible();
  await expect(table.getByRole("columnheader", { name: "DNS" })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the list of names");

  await page.getByRole("button", { name: "Add a name" }).first().click();
  const dialog = page.getByRole("dialog", { name: "Add a name" });
  // What the name is for is the first question, and an application is the answer most of the time.
  await expect(dialog.getByRole("radio", { name: /For an application/ })).toBeChecked();
  await expect(dialog.getByRole("radio", { name: /A site of its own/ })).toBeVisible();
  await expect(dialog.getByRole("radio", { name: /Redirect to another name/ })).toBeVisible();
  await dialog.getByRole("combobox", { name: "Application" }).click();
  await page.getByRole("option", { name: app, exact: true }).click();

  // DNS is asked when the operator leaves the field, and the verdict is said where the name was typed.
  const lookup = page.waitForResponse((response) => response.url().includes("/api/domains/dns") && response.url().includes(name));
  await dialog.getByRole("textbox", { name: "Name", exact: true }).fill(name);
  await dialog.getByRole("textbox", { name: "Name", exact: true }).blur();
  expect((await lookup).status()).toBe(200);
  await expect(dialog.getByText(`${name} points here`)).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the dialog that adds a name");

  const added = page.waitForRequest((request) => request.url().endsWith(`/api/apps/${app}/domains`) && request.method() === "POST");
  await dialog.getByRole("button", { name: "Add the name" }).click();
  expect((await added).postDataJSON()).toEqual({ domain: name, kind: "alias", force: false, dns: true });
  await expect(dialog).toBeHidden();
  // The row appears in the list, and the certificate job is followed on the page.
  await expect(table.getByRole("row").filter({ hasText: name })).toBeVisible();
  await expect(page.getByText("The certificate covers the new name")).toBeVisible({ timeout: 20_000 });
  await stillness(page);

  // The server is left as it was seeded: other specs on the worker count its names.
  await elevate(page, consoleServer);
  const removed = await page.request.delete(`/api/apps/${app}/domains/${name}`, { headers: await csrf(page) });
  expect(removed.status(), await removed.text()).toBeLessThan(300);
});

test("a name for a site of its own is made in the same dialog, with the template and the port beside it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/domains?new=true");
  const dialog = page.getByRole("dialog", { name: "Add a name" });
  await expect(dialog).toBeVisible();
  // The link that opened it is spent: reloading the page does not open it again.
  await expect(page).toHaveURL(/\/domains$/);
  await dialog.getByRole("radio", { name: /A site of its own/ }).check();
  await expect(dialog.getByRole("radio", { name: /Reverse proxy/ })).toBeVisible();
  await expect(dialog.getByRole("combobox", { name: "Application" })).toHaveCount(0);
  await stillness(page);
  await expectNoA11yViolations(page, "the dialog that adds a name for a site");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();
});

test("a port is opened from Published ports in one dialog that says what will happen before it does it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/network");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Published ports");
  await page.getByRole("button", { name: "Open a port" }).first().click();
  const dialog = page.getByRole("alertdialog", { name: "Open a port" });
  await expect(dialog.getByRole("radio", { name: /A game or a service/ })).toBeChecked();
  await expect(dialog.getByRole("radio", { name: /A database/ })).toBeVisible();

  // Nothing listens on the Minecraft port: it is opened all the same, and the plan says so in plain words.
  await dialog.getByRole("textbox", { name: "Port", exact: true }).fill("25565");
  await expect(dialog.getByText("Nothing listens on 25565 now: it is opened all the same, and answers when the program starts.")).toBeVisible();
  await expect(dialog.getByText("Server firewall: allow TCP 25565 from anyone.")).toBeVisible();
  // The provider's own firewall is not Noust's to see: the dialog says it before it is a surprise.
  await expect(dialog.getByText(/If your provider has its own firewall/)).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the dialog that opens a port");

  // A port that is not a port is said where it was typed, and nothing is asked of the server.
  await dialog.getByRole("textbox", { name: "Port", exact: true }).fill("70000");
  await expect(dialog.getByText("A port is a number from 1 to 65535.")).toBeVisible();
  await dialog.getByRole("textbox", { name: "Port", exact: true }).fill("25565");

  await elevate(page, consoleServer);
  const rule = page.waitForRequest((request) => request.url().endsWith("/api/server/security/firewall/rules") && request.method() === "POST");
  await dialog.getByRole("button", { name: "Open 25565" }).click();
  expect((await rule).postDataJSON()).toEqual({ action: "allow", port: 25565, proto: "tcp", source: "any", comment: "" });
  await expect(dialog).toBeHidden();
  // A firewall change undoes itself unless it is kept: the page says so, and the operator can take it back at once. The
  // sandbox's firewall is one fixed answer, so the change is undone here to leave the worker's server as it was seeded.
  await expect(page.getByText(/A firewall change undoes itself in/)).toBeVisible();
  await page.getByRole("button", { name: "Undo now" }).click();
  await page.getByRole("alertdialog", { name: "Undo the change now?" }).getByRole("button", { name: "Undo the change" }).click();
  await expect(page.getByText(/A firewall change undoes itself in/)).toBeHidden();
});

test("who listens on 5432 and whether it can be reached from outside is one table, and a shut port offers to be opened", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/network/listening?q=5432");
  const table = page.getByRole("table", { name: "Ports and what uses them" });
  // The program the kernel names, what holds the port in Noust, and the firewall's verdict, in one row.
  const row = table.getByRole("row").filter({ hasText: "5432" });
  await expect(row).toContainText("postgres");
  await expect(row).toContainText("Exposed");
  await settle(page);
  await expectNoA11yViolations(page, "who listens on a port");

  // MySQL listens only inside the machine: no rule of the firewall reaches it, and the row offers the one dialog.
  await page.getByRole("searchbox", { name: "Search ports" }).fill("3306");
  const shut = table.getByRole("row").filter({ hasText: "mysqld" });
  await shut.getByRole("button", { name: "Open to the outside…" }).click();
  const dialog = page.getByRole("alertdialog", { name: "Open a port" });
  await expect(dialog.getByRole("textbox", { name: "Port", exact: true })).toHaveValue("3306");
  await expect(dialog.getByText(/It listens only inside this machine \(127\.0\.0\.1\)/)).toBeVisible();
  // It answers on another port, because nginx cannot take the one the program already holds.
  await expect(dialog.getByRole("textbox", { name: "Port to answer on" })).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the dialog that opens a port the program holds inside");
  await dialog.getByRole("button", { name: "Cancel" }).click();
  await expect(dialog).toBeHidden();

  // A port nothing uses says it is free, and offers the same dialog.
  await page.getByRole("searchbox", { name: "Search ports" }).fill("25565");
  await expect(page.getByText("Nothing on this server uses port 25565.")).toBeVisible();
  await page.getByRole("button", { name: "Open port 25565" }).click();
  await expect(page.getByRole("alertdialog", { name: "Open a port" }).getByRole("textbox", { name: "Port", exact: true })).toHaveValue("25565");
  await page.keyboard.press("Escape");
});

test("the firewall page says who made each rule and names the other two firewalls apart from this one", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/network/firewall");
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Server firewall");
  await expect(page.getByText("Active", { exact: true }).first()).toBeVisible();
  const rules = page.getByRole("table", { name: /rules/i }).first();
  // A rule an operator wrote by hand says so, and its comment is theirs, verbatim.
  const migration = rules.getByRole("row").filter({ hasText: "temporary: migration from the old host" });
  await expect(migration).toContainText("5432");
  // One that has no note says it was made by hand, and one that has says it in the operator's own words.
  await expect(rules.getByText("By hand").first()).toBeVisible();

  const three = page.getByRole("heading", { level: 2, name: "It is not the only firewall" });
  await expect(three).toBeVisible();
  await expect(page.getByText("Web (WAF)")).toBeVisible();
  await expect(page.getByText("Provider's")).toBeVisible();
  await expect(page.getByText("If a port open here does not answer from outside, open it there too.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the firewall page");

  // From here a port is opened with the same dialog as everywhere.
  await page.getByRole("button", { name: "Open a port" }).first().click();
  await expect(page.getByRole("alertdialog", { name: "Open a port" })).toBeVisible();
  await page.keyboard.press("Escape");
});

test("the nginx of a domain is changed from the list of names: the Sites section, the site, its editor", async ({ page, consoleServer }) => {
  const site = "example.com";
  await signIn(page, consoleServer, "/domains");
  const sections = page.getByRole("navigation", { name: "Domains sections" });
  await sections.getByRole("link", { name: /^Sites/ }).click();
  await expect(page).toHaveURL(/\/domains\/sites$/);
  const sites = page.getByRole("region", { name: "Web server sites" });
  // Who wrote each file, and which application it belongs to, are columns of the list.
  await expect(sites.getByRole("columnheader", { name: "Written by" })).toBeVisible();
  await expect(sites.getByRole("columnheader", { name: "Belongs to" })).toBeVisible();
  // The site's own name is the first link of its row; the second is the application it belongs to.
  await sites.getByRole("link", { name: site, exact: true }).first().click();
  await expect(page).toHaveURL(new RegExp(`/domains/sites/${site.replace(".", "\\.")}$`));
  const editor = page.getByRole("textbox", { name: `Configuration of ${site}` });
  await expect(editor).toHaveValue(/server_name example\.com;/);
  await settle(page);
  await expectNoA11yViolations(page, "a site's editor reached from the list of names");
});

test("the list of names and Network lead to each other: the footer of the one opens the other", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/domains");
  await page.getByRole("main").getByRole("link", { name: "opening a port or a tunnel" }).click();
  await expect(page).toHaveURL(/\/network$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Published ports");
  await expect(page.getByRole("navigation", { name: "Network sections" })).toBeVisible();
});

test("on a phone the Network pages keep to the screen and the dialogs fit", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await signIn(page, consoleServer, "/network");
  for (const path of ["/network", "/network/listening", "/network/firewall", "/network/tunnels", "/network/interfaces", "/domains", "/domains/sites", "/domains/certificates", "/domains/dns"]) {
    await page.goto(path);
    await expect(page.getByRole("heading", { level: 1 })).toBeVisible();
    await settle(page);
    const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
    expect(overflow, path).toBeLessThanOrEqual(0);
  }
  await page.goto("/network?new=true");
  const dialog = page.getByRole("alertdialog", { name: "Open a port" });
  await expect(dialog).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the dialog that opens a port, on a phone");
  const box = await dialog.boundingBox();
  if (box === null) throw new Error("the dialog is not laid out");
  expect(box.x).toBeGreaterThanOrEqual(0);
  expect(box.x + box.width).toBeLessThanOrEqual(390);
});
