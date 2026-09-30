/**
 * A verdict never stands without its reasons, against the real backend.
 *
 * Server > Overview judges the machine from the one list it shows, "What needs attention", so
 * the state beside the title and the list below it never disagree: the seeded machine is
 * critical (a database reachable from the internet) with warnings (pending security updates,
 * a full disk, a reboot due, no swap), each with the level as a word and where it is fixed.
 *
 * Certificates are not the server's own state: they are named under the Overview's "Needs
 * attention". A console server started with --expired-certificate has the first certificate
 * expired; its name links to the certificates page filtered to it.
 */

import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer } from "./fixtures";

test("the server's verdict stands beside the reasons that make it, each with where it is fixed", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/server");
  const main = page.getByRole("main");
  await expect(main.getByRole("heading", { level: 1, name: "Server" })).toBeVisible();
  const reasons = main.getByRole("list", { name: "What needs attention" });
  await expect(reasons.getByRole("listitem").first()).toBeVisible();
  // The worst reason decides the verdict, and it comes first.
  await expect(main.getByText("Critical", { exact: true }).first()).toBeVisible();
  await expect(reasons.getByRole("listitem").first()).toContainText(/^Critical/);
  // Every reason carries its level as a word and one way to fix it.
  for (const item of await reasons.getByRole("listitem").all()) {
    await expect(item).toContainText(/^(Critical|Warning)\S/);
    await expect(item.getByRole("link").or(item.getByRole("button"))).toHaveCount(1);
  }
  const exposed = reasons.getByRole("listitem").filter({ hasText: "A database or internal service is reachable from the internet" });
  await expect(exposed.getByRole("link", { name: "Review" })).toHaveAttribute("href", "/server/security?view=firewall");
  const disk = reasons.getByRole("listitem").filter({ hasText: /^Warning\/srv is \d+% full/ });
  await expect(disk.getByRole("link", { name: "Free space" })).toHaveAttribute("href", "/server/storage");
  await settle(page);
  await expectNoA11yViolations(page, "the server's verdict and its reasons");
});

const withExpiredCertificate = test.extend<object, { consoleServer: ConsoleServer }>({
  consoleServer: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const server = await startConsoleServer(["--expired-certificate"]);
      try {
        await use(server);
      } finally {
        await server.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
});

withExpiredCertificate("an expired certificate is named under Needs attention and links to where it is renewed", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/");
  const attention = page.getByRole("region", { name: "Needs attention" });
  // example.com is an application's name: the expiry is one more reason on that application.
  const issue = attention.getByRole("listitem").filter({ has: page.getByRole("link", { name: "example.com", exact: true }) }).first();
  await expect(issue.getByText("Certificate expired", { exact: true })).toBeVisible();
  const renew = issue.getByRole("link", { name: "Certificate of example.com" });
  await expect(renew).toHaveAttribute("href", "/apps/example.com/domains");
  await settle(page);
  await expectNoA11yViolations(page, "an expired certificate under Needs attention");

  await renew.click();
  await expect(page).toHaveURL(/\/apps\/example\.com\/domains$/);
  await expect(page.getByRole("main").getByText(/Expired/).first()).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the application's domains, its certificate expired");
});
