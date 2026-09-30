/**
 * An application's Metrics tab against the real backend: CPU and memory from one range read
 * (seeded thirty days deep for tienda.example.org), with its limit and deploys marked, and, for
 * every application that cannot be measured, why and how to fix it instead of empty charts. In
 * the sandbox no application has a real cgroup, so every unit says so, verbatim; a static site
 * says it has no process, and why its requests are not counted. Both themes, with the CSP and
 * console gates of the `problems` fixture.
 */

import { expect, expectNoA11yViolations, settle, signIn, test } from "./fixtures";

const DOMAIN = "tienda.example.org";

test("the charts say what they show; earlier readings stay while new ones are not recorded", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${DOMAIN}/metrics`);
  const cpu = page.getByRole("img", { name: /^CPU, last 24 hours, 1-minute averages/ });
  await expect(cpu).toBeVisible();
  await expect(page.getByRole("img", { name: /^Memory, last 24 hours/ })).toBeVisible();
  // The seed's thirty days, read from its own tiers: the sentence says it, not the button.
  await expect(page.locator("[data-truth]")).toHaveText(/^Showing .+, 1-minute averages\./);
  // Why nothing new is recorded, and the command that shows systemd's view of the service.
  const reason = page.locator("[data-metrics-reason]");
  await expect(reason).toHaveAttribute("data-metrics-reason", "cgroup_missing");
  await expect(reason.getByText("systemctl status tienda-example-org")).toBeVisible();

  // Deploys in the window are marked on the chart and listed under it.
  const cpuChart = page.locator("figure").filter({ has: page.getByRole("heading", { name: "CPU", exact: true }) });
  const marked = cpuChart.getByRole("link", { name: /^Deploy \d+, / });
  await expect(marked.first()).toBeVisible();
  await expect(page.getByRole("group", { name: "Deploys in this range" }).getByRole("link").first()).toBeVisible();
  await expect(cpu).toHaveAccessibleName(/\d+ markers? in view\.$/);
  await settle(page);
  await expectNoA11yViolations(page, "an app's metrics");

  await page.getByRole("radio", { name: "7d" }).click();
  await expect(page).toHaveURL(/[?&]range=7d/);
  await expect(page.getByRole("img", { name: /^CPU, last 7 days, 10-minute averages/ })).toBeVisible();
  await page.getByRole("radio", { name: "24h" }).click();
  await expect(page).not.toHaveURL(/range=/);
});

test("a deploy marker on the chart names the deploy and opens it", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, `/apps/${DOMAIN}/metrics`);
  const cpuChart = page.locator("figure").filter({ has: page.getByRole("heading", { name: "CPU", exact: true }) });
  await expect(cpuChart.getByRole("img")).toBeVisible();
  const marker = cpuChart.getByRole("link", { name: /^Deploy \d+, / }).first();
  await expect(marker).toBeVisible();
  const name = await marker.getAttribute("aria-label");
  await marker.click();
  await expect(page).toHaveURL(/\/apps\/[^/]+\/deployments\/\d+/);
  const id = /^Deploy (\d+),/.exec(name ?? "")?.[1];
  await expect(page.getByRole("heading", { level: 2, name: `Deployment ${id ?? ""}` })).toBeVisible();
});

test("an application with no reading says why, and how to fix it, instead of empty charts", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps/example.com/metrics");
  const reason = page.locator("[data-metrics-reason]");
  await expect(reason).toHaveAttribute("data-metrics-reason", "cgroup_missing");
  await expect(reason.getByRole("heading", { name: "Where the service is measured could not be found" })).toBeVisible();
  await expect(reason.getByText("systemctl status wasm-example-com")).toBeVisible();
  await expect(page.getByRole("img", { name: /^CPU/ })).toHaveCount(0);
  await settle(page);
  await expectNoA11yViolations(page, "an app's metrics with no reading");
});

test("a static site has no process, and says why its requests are not counted", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/apps/bodas.example.com/metrics?range=30d");
  await expect(page.getByRole("heading", { level: 2, name: "Traffic" })).toBeVisible();
  const reason = page.locator("[data-metrics-reason]");
  await expect(reason).toHaveAttribute("data-metrics-reason", "access_log_missing");
  await expect(reason.getByRole("heading", { name: "Requests are not being counted" })).toBeVisible();
  // What the system was asked, verbatim.
  await expect(reason.getByText(/looked for: \/var\/log\/nginx\/bodas\.example\.com\.access\.log/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a static site's metrics tab");
});
