/**
 * Blue/green activation in an application's Settings, against the real engine: the seeded
 * pagos.example.org runs as two instances with green serving, is turned off (back to one unit)
 * and on again, each through its confirmation and the real "Confirm it's you", each followed
 * as a job until the backend reports it done. The console server models the instances' units
 * and answers their health checks, so the switches are the ones `noust app zero-downtime` runs.
 *
 * Tests in a worker share one server, so the test leaves the app as it found it: on, green
 * serving, the seeded drain. An app deployed in place cannot use the mode, and says why.
 */

import type { Page } from "@playwright/test";

import { confirmItsYou, expect, expectNoA11yViolations, signIn, stillness, test, toasts } from "./fixtures";

const ZD_APP = "pagos.example.org";
const UNIT = "pagos-example-org";
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

/** The panel, a region named by its heading. */
function panel(page: Page) {
  return page.getByRole("region", { name: "Zero downtime", exact: true });
}

test("blue/green is turned off and on again, each switch confirmed and followed to the end", async ({ page, consoleServer }) => {
  test.setTimeout(150_000);
  await page.setViewportSize({ width: 1440, height: 1000 });
  const seeded = await (async () => {
    await signIn(page, consoleServer, `/apps/${ZD_APP}/settings`);
    return zeroDowntimeOf(page, ZD_APP);
  })();
  expect(seeded.enabled, "the seed leaves the app in zero-downtime mode").toBe(true);
  expect(seeded.active_color).toBe("green");

  const zd = panel(page);
  await expect(zd.getByText("On", { exact: true })).toBeVisible();
  const instances = zd.getByRole("list", { name: "Instances" });
  await expect(instances.getByRole("listitem")).toHaveCount(2);
  const green = instances.getByRole("listitem").filter({ hasText: `${UNIT}@green.service` });
  const blue = instances.getByRole("listitem").filter({ hasText: `${UNIT}@blue.service` });
  await expect(green.getByText("Serving", { exact: true })).toBeVisible();
  await expect(blue.getByText("Idle", { exact: true })).toBeVisible();
  const greenPort = seeded.instances.find((instance) => instance.color === "green")?.port;
  expect(seeded.upstream_port).toBe(greenPort);
  await expect(zd.getByText(`nginx's upstream names port ${String(greenPort)}.`)).toBeVisible();
  await expect(zd.getByLabel("Drain")).toHaveValue(DRAIN);
  await zd.scrollIntoViewIfNeeded();
  await stillness(page);
  await expectNoA11yViolations(page, "an app in zero-downtime mode");

  // Off: "Confirm it's you" first, then what going back to one unit means.
  await zd.getByRole("button", { name: "Turn off zero downtime" }).click();
  await confirmItsYou(page, consoleServer);
  const off = page.getByRole("dialog", { name: `Turn off zero downtime for ${ZD_APP}?` });
  await expect(off).toBeVisible();
  await expect(off).toContainText("drops connections for a moment");
  await stillness(page);
  await expectNoA11yViolations(page, "the turn-off confirmation");
  const turnedOff = page.waitForResponse(
    (r) => r.url().endsWith(`/api/apps/${ZD_APP}/zero-downtime`) && r.request().method() === "PUT",
  );
  await off.getByRole("button", { name: "Turn off zero downtime" }).click();
  const offRequest = await turnedOff;
  expect(offRequest.status()).toBe(202);
  expect(offRequest.request().postDataJSON()).toMatchObject({ enabled: false });
  await expect(off).toBeHidden({ timeout: SWITCH_TIMEOUT });
  await expect(toasts(page).getByText(`Zero downtime is off for ${ZD_APP}`)).toBeVisible();
  await expect(zd.getByText("Off", { exact: true })).toBeVisible();
  await expect(zd.getByRole("list", { name: "Instances" })).toHaveCount(0);
  const afterOff = await zeroDowntimeOf(page, ZD_APP);
  expect(afterOff).toMatchObject({ enabled: false, active_color: null, instances: [], upstream_port: null });

  // The app runs as its own unit again, and answers.
  const app = (await (await page.request.get(`/api/apps/${ZD_APP}`)).json()) as { zero_downtime: boolean; status: string; unit: string };
  expect(app).toMatchObject({ zero_downtime: false, status: "running", unit: UNIT });

  // On: the drain is asked for, and the dialog says what two copies at once mean.
  await zd.getByLabel("Drain").fill(DRAIN);
  await zd.getByRole("button", { name: "Turn on zero downtime" }).click();
  const on = page.getByRole("dialog", { name: `Turn on zero downtime for ${ZD_APP}?` });
  await expect(on).toBeVisible();
  await expect(on).toContainText(`stop the old one ${DRAIN} seconds later`);
  await expect(on.getByText("Two copies of the app run at once for a few seconds.")).toBeVisible();
  await stillness(page);
  await expectNoA11yViolations(page, "the turn-on confirmation");
  const turnedOn = page.waitForResponse(
    (r) => r.url().endsWith(`/api/apps/${ZD_APP}/zero-downtime`) && r.request().method() === "PUT",
  );
  await on.getByRole("button", { name: "Turn on zero downtime" }).click();
  const onRequest = await turnedOn;
  expect(onRequest.status()).toBe(202);
  expect(onRequest.request().postDataJSON()).toMatchObject({ enabled: true, drain_seconds: Number(DRAIN) });
  await expect(on).toBeHidden({ timeout: SWITCH_TIMEOUT });
  await expect(toasts(page).getByText(`Zero downtime is on for ${ZD_APP}`)).toBeVisible();
  await expect(zd.getByText("On", { exact: true })).toBeVisible();
  await expect(zd.getByRole("list", { name: "Instances" }).getByRole("listitem")).toHaveCount(2);
  await expect(
    zd.getByRole("list", { name: "Instances" }).getByRole("listitem").filter({ hasText: `${UNIT}@green.service` }).getByText("Serving", { exact: true }),
  ).toBeVisible();

  // As found: on, green serving, the seeded drain, the upstream on green's port.
  const restored = await zeroDowntimeOf(page, ZD_APP);
  expect(restored).toMatchObject({ enabled: true, active_color: "green", drain_seconds: Number(DRAIN), upstream_port: greenPort });
  await stillness(page);
  await expectNoA11yViolations(page, "zero downtime turned back on");
});

test("an app deployed in place says why it cannot use zero downtime, and offers nothing to try", async ({ page, consoleServer }) => {
  await page.setViewportSize({ width: 1440, height: 1000 });
  await signIn(page, consoleServer, `/apps/${IN_PLACE_APP}/settings`);
  const status = await zeroDowntimeOf(page, IN_PLACE_APP);
  expect(status.enabled).toBe(false);

  const zd = panel(page);
  await expect(zd.getByText(`${IN_PLACE_APP} is deployed in place; blue/green runs two releases side by side`)).toBeVisible();
  await expect(zd.getByText(`Move it onto releases first: noust app migrate ${IN_PLACE_APP}`)).toBeVisible();
  await expect(zd.getByText("Off", { exact: true })).toBeVisible();
  await expect(zd.getByRole("button", { name: /zero downtime/ })).toHaveCount(0);
  await expect(zd.getByLabel("Drain")).toHaveCount(0);
  await zd.scrollIntoViewIfNeeded();
  await stillness(page);
  await expectNoA11yViolations(page, "an app that cannot use zero downtime");
});
