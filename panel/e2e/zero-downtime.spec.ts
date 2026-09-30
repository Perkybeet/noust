/**
 * Zero-downtime deploys in an application's Deploys settings, against the real engine: the seeded
 * pagos.example.org runs as two instances with green serving, is turned off (back to one unit)
 * and on again, each through its confirmation and the real "Confirm it's you", each followed
 * as a job until the backend reports it done. The console server models the instances' units
 * and answers their health checks, so the switches are the ones `noust app zero-downtime` runs.
 *
 * Tests in a worker share one server, so the test leaves the app as it found it: on, green
 * serving, the seeded drain. An app in a single folder is told it needs instant rollback first.
 */

import type { Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, signIn, stillness, test } from "./fixtures";

const ZD_APP = "pagos.example.org";
const IN_PLACE_APP = "shop.example.net";
/** What the seed recorded, and what the test puts back. */
const DRAIN = "2";

/** A switch starts an instance, probes it, moves nginx and waits out the drain. */
const SWITCH_TIMEOUT = 45_000;

interface ZeroDowntime {
  enabled: boolean;
  active_color: string | null;
  drain_seconds: number;
  instances: { color: string; serving: boolean; state: string; port: number }[];
  upstream_port: number | null;
}

async function zeroDowntimeOf(page: Page, domain: string): Promise<ZeroDowntime> {
  return (await (await page.request.get(`/api/apps/${domain}/zero-downtime`)).json()) as ZeroDowntime;
}

/** The card, by its title. */
function panel(page: Page) {
  return page.locator("section").filter({ has: page.getByRole("heading", { name: "Zero-downtime deploys", exact: true }) }).last();
}

test("zero-downtime deploys are turned off and on again, each confirmed and followed to the end", async ({ page, consoleServer }) => {
  test.setTimeout(150_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const seeded = await (async () => {
    await signIn(page, consoleServer, `/apps/${ZD_APP}/settings/deploys`);
    return zeroDowntimeOf(page, ZD_APP);
  })();
  expect(seeded.enabled, "the seed leaves the app in zero-downtime mode").toBe(true);
  expect(seeded.active_color).toBe("green");

  const zd = panel(page);
  await expect(zd.getByText("On", { exact: true })).toBeVisible();
  const copies = zd.getByRole("list", { name: "Copies of the app" });
  await expect(copies.getByRole("listitem")).toHaveCount(2);
  const green = copies.getByRole("listitem").filter({ hasText: "Green" });
  const blue = copies.getByRole("listitem").filter({ hasText: "Blue" });
  await expect(green.getByText("Live", { exact: true })).toBeVisible();
  await expect(blue.getByText("Idle", { exact: true })).toBeVisible();
  const greenPort = seeded.instances.find((instance) => instance.color === "green")?.port;
  expect(seeded.upstream_port).toBe(greenPort);
  await expect(zd.getByLabel("Keep the old version for")).toHaveValue(DRAIN);
  await zd.scrollIntoViewIfNeeded();
  await stillness(page);
  await expectNoA11yViolations(page, "an app with zero-downtime deploys");

  // Off: "Confirm it's you" first, then what going back to one copy means.
  await zd.getByRole("button", { name: "Turn off" }).click();
  await confirmItsYou(page, consoleServer);
  const off = page.getByRole("alertdialog", { name: `Turn off zero-downtime deploys for ${ZD_APP}?` });
  await expect(off).toBeVisible();
  await expect(off).toContainText("drops connections for a moment");
  await stillness(page);
  await expectNoA11yViolations(page, "the turn-off confirmation");
  const turnedOff = page.waitForResponse((r) => r.url().endsWith(`/api/apps/${ZD_APP}/zero-downtime`) && r.request().method() === "PUT");
  await off.getByRole("button", { name: "Turn off" }).click();
  const offRequest = await turnedOff;
  expect(offRequest.status()).toBe(202);
  expect(offRequest.request().postDataJSON()).toMatchObject({ enabled: false });
  // The dialog waits for the job, not just for it to be queued.
  await expect(off).toBeHidden({ timeout: SWITCH_TIMEOUT });
  await expect(zd.getByText("Off", { exact: true })).toBeVisible();
  await expect(zd.getByRole("list", { name: "Copies of the app" })).toHaveCount(0);
  const afterOff = await zeroDowntimeOf(page, ZD_APP);
  expect(afterOff).toMatchObject({ enabled: false, active_color: null, instances: [], upstream_port: null });
  const app = (await (await page.request.get(`/api/apps/${ZD_APP}`)).json()) as { zero_downtime: boolean; status: string };
  expect(app).toMatchObject({ zero_downtime: false, status: "running" });

  // On: what two copies at once mean, and how long the old one stays.
  await zd.getByRole("button", { name: "Turn on…" }).click();
  const on = page.getByRole("dialog", { name: `Turn on zero-downtime deploys for ${ZD_APP}?` });
  await expect(on).toBeVisible();
  await expect(on.getByText("Two copies of the app run at once for a few seconds")).toBeVisible();
  await on.getByLabel("Keep the old version for").fill(DRAIN);
  await stillness(page);
  await expectNoA11yViolations(page, "the turn-on dialog");
  const turnedOn = page.waitForResponse((r) => r.url().endsWith(`/api/apps/${ZD_APP}/zero-downtime`) && r.request().method() === "PUT");
  await on.getByRole("button", { name: "Turn on" }).click();
  const onRequest = await turnedOn;
  expect(onRequest.status()).toBe(202);
  expect(onRequest.request().postDataJSON()).toMatchObject({ enabled: true, drain_seconds: Number(DRAIN) });
  await expect(on).toBeHidden({ timeout: SWITCH_TIMEOUT });
  await expect(zd.getByText("On", { exact: true })).toBeVisible();
  await expect(zd.getByRole("list", { name: "Copies of the app" }).getByRole("listitem")).toHaveCount(2);
  await expect(zd.getByRole("list", { name: "Copies of the app" }).getByRole("listitem").filter({ hasText: "Green" }).getByText("Live", { exact: true })).toBeVisible();

  // As found: on, green serving, the seeded drain, the upstream on green's port.
  const restored = await zeroDowntimeOf(page, ZD_APP);
  expect(restored).toMatchObject({ enabled: true, active_color: "green", drain_seconds: Number(DRAIN), upstream_port: greenPort });
  await stillness(page);
  await expectNoA11yViolations(page, "zero-downtime deploys turned back on");
});

test("an app in a single folder is told it needs instant rollback first, and offered nothing to try", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, `/apps/${IN_PLACE_APP}/settings/deploys`);
  const status = await zeroDowntimeOf(page, IN_PLACE_APP);
  expect(status.enabled).toBe(false);

  const zd = panel(page);
  await expect(zd.getByText("They need instant rollback, above: turn that on first.")).toBeVisible();
  await expect(zd.getByRole("button")).toHaveCount(0);
  await expect(zd.getByLabel("Keep the old version for")).toHaveCount(0);
  await zd.scrollIntoViewIfNeeded();
  await stillness(page);
  await expectNoA11yViolations(page, "an app that cannot have zero-downtime deploys yet");
});
