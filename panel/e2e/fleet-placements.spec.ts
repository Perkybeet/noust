/**
 * An application on several servers, and what a server's menu adds, against two real backends: a
 * central and a node (see fleet.spec.ts for how they are joined without ssh). The central compares the
 * node's own application with what it recorded, plans a rollout through the node's real API, and keeps
 * the options of how it connects to the node.
 *
 * - a recorded placement is compared on the page, each server a row linking to its page there;
 * - a rollout is planned from the page, and nothing is deployed until it is asked for;
 * - the servers are written for a load balancer in the format chosen;
 * - the connection options of a server open with their default, refuse what is not valid and
 *   leave the server untouched;
 * - the server that builds an application for the others is chosen from the page, and a shipment
 *   whose builder cannot build releases is refused with the reason when it is planned, before
 *   anything is built.
 */

import { csrf } from "./apps-cleanup";
import { expect, expectNoA11yViolations, settle, signIn, startConsoleServer, test } from "./fixtures";
import type { ConsoleServer, RunningServer } from "./fixtures";

const NODE_NAME = "fleet-node";

/**
 * A second name for the same node process: the build server of a shipment is another server than
 * the ones that run the application, and one process is all the sandbox has to answer for both.
 */
const BUILDER_NAME = "build-1";

/** Seeded only on the node (see NOUST_E2E_FLEET_APP): the placement names it, so the node answers for it. */
const NODE_APP = "node-only.example.net";

function portOf(server: ConsoleServer): string {
  return new URL(server.url).port;
}

interface FleetWorkerFixtures {
  node: RunningServer;
  consoleServer: ConsoleServer;
}

const withFleet = test.extend<object, FleetWorkerFixtures>({
  node: [
    // eslint-disable-next-line no-empty-pattern -- Playwright requires the destructuring form
    async ({}, use) => {
      const node = await startConsoleServer([], { env: { NOUST_E2E_FLEET_APP: NODE_APP } });
      try {
        await use(node);
      } finally {
        await node.stop();
      }
    },
    { scope: "worker", timeout: 75_000 },
  ],
  consoleServer: [
    async ({ node }, use) => {
      const central = await startConsoleServer([
        "--totp",
        "--backup-codes",
        "500",
        "--fleet-node",
        `${NODE_NAME}=127.0.0.1:${portOf(node)}`,
        "--fleet-node",
        `${BUILDER_NAME}=127.0.0.1:${portOf(node)}`,
      ]);
      try {
        await use(central);
      } finally {
        await central.stop();
      }
    },
    { scope: "worker", timeout: 90_000 },
  ],
});

withFleet("a recorded placement is compared on the page, and its servers are written for a load balancer", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/fleet/servers");
  const saved = await page.request.put(`/api/fleet/placements/${NODE_APP}`, {
    data: { nodes: [NODE_NAME], labels: {}, serial: 1, max_failures: null, canary: null, rollback_on_failure: true },
    headers: await csrf(page),
  });
  expect(saved.ok(), await saved.text()).toBe(true);

  await page.getByRole("navigation", { name: "Fleet views" }).getByRole("link", { name: "On several servers" }).click();
  await expect(page).toHaveURL(/\/fleet\/placements$/);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("Fleet");
  await expect(page.getByRole("heading", { level: 3, name: NODE_APP })).toBeVisible();
  // A desktop's width: the servers are a table (a phone's are card rows, a list).
  const servers = page.getByRole("table", { name: `${NODE_APP} on each of its servers` });
  await expect(servers.getByRole("link", { name: NODE_NAME })).toHaveAttribute("href", `/n/${NODE_NAME}/apps/${NODE_APP}`);
  await settle(page);
  await expectNoA11yViolations(page, "the applications on several servers");

  await page.getByRole("button", { name: `Actions for ${NODE_APP}` }).click();
  await page.getByRole("menuitem", { name: "Servers for the load balancer" }).click();
  const dialog = page.getByRole("dialog", { name: `Servers of ${NODE_APP} for the load balancer` });
  await expect(dialog.getByText(/upstream node_only_example_net \{/)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the load balancer dialog");
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

withFleet("a rollout is planned from the page and nothing is deployed until it is asked for", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/fleet/placements");
  const saved = await page.request.put(`/api/fleet/placements/${NODE_APP}`, {
    data: { nodes: [NODE_NAME], labels: {}, serial: 1, max_failures: null, canary: null, rollback_on_failure: true },
    headers: await csrf(page),
  });
  expect(saved.ok(), await saved.text()).toBe(true);
  await page.reload();

  await page.getByRole("button", { name: `Actions for ${NODE_APP}` }).click();
  await page.getByRole("menuitem", { name: "Roll out" }).click();
  const dialog = page.getByRole("dialog", { name: `Roll out ${NODE_APP}` });
  await dialog.getByRole("button", { name: "Show the plan" }).click();
  await expect(dialog.getByRole("button", { name: /^Roll out on \d+ servers?$/ })).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a rollout's plan");

  // The plan only plans: no deployment of the application exists because of it.
  const deployments = await page.request.get(`/api/fleet/jobs`);
  expect(deployments.ok()).toBe(true);
  const listed = (await deployments.json()) as { items?: { action?: string }[] };
  expect((listed.items ?? []).filter((job) => job.action === "placement_rollout")).toHaveLength(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});

withFleet("a server's connection options open with their default, refuse what is not valid and change nothing", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/fleet/servers");
  const servers = page.getByRole("region", { name: "Servers of this fleet" });
  await servers.getByRole("button", { name: `Actions for ${NODE_NAME}` }).click();
  await page.getByRole("menuitem", { name: "Connection options" }).click();

  const dialog = page.getByRole("dialog", { name: `How this central connects to ${NODE_NAME}` });
  const wait = dialog.getByLabel(/Wait for the connection/);
  await expect(wait).toBeVisible();
  await expect(wait).toHaveValue("");
  await settle(page);
  await expectNoA11yViolations(page, "the connection options");

  await wait.fill("900");
  await dialog.getByRole("checkbox", { name: /Connect through a bastion/ }).click();
  await dialog.getByLabel(/^Bastion/).fill("not a bastion");
  await dialog.getByRole("button", { name: "Save and test" }).click();
  await expect(dialog.getByText("Write a whole number of seconds from 1 to 300.")).toBeVisible();
  await expect(dialog.getByText("Write the bastion as user@host or user@host:port.")).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "the connection options, refused");

  // Nothing was sent: the node is reached exactly as before.
  const options = await page.request.get(`/api/nodes/${NODE_NAME}/ssh`);
  expect(options.ok(), await options.text()).toBe(true);
  const body = (await options.json()) as { connect_timeout: number | null; bastion: unknown };
  expect(body.connect_timeout).toBeNull();
  expect(body.bastion).toBeNull();
});

withFleet("the build server is chosen from the page and recorded, and no server is touched", async ({ page, consoleServer }) => {
  await signIn(page, consoleServer, "/fleet/placements");
  const saved = await page.request.put(`/api/fleet/placements/${NODE_APP}`, {
    data: { nodes: [NODE_NAME], labels: {}, serial: 1, max_failures: null, canary: null, rollback_on_failure: true },
    headers: await csrf(page),
  });
  expect(saved.ok(), await saved.text()).toBe(true);
  await page.reload();

  await page.getByRole("button", { name: `Actions for ${NODE_APP}` }).click();
  await page.getByRole("menuitem", { name: "Choose the build server" }).click();
  const dialog = page.getByRole("dialog", { name: `Build server of ${NODE_APP}` });
  await dialog.getByRole("combobox", { name: "Build server" }).click();
  await page.getByRole("option", { name: BUILDER_NAME, exact: true }).click();
  await settle(page);
  await expectNoA11yViolations(page, "the build server dialog");
  await dialog.getByRole("button", { name: "Save" }).click();
  await expect(dialog).toBeHidden();

  await expect(page.getByText(`Built on ${BUILDER_NAME}`)).toBeVisible();
  const recorded = await page.request.get(`/api/fleet/builders/${NODE_APP}`);
  expect(recorded.ok(), await recorded.text()).toBe(true);
  expect(((await recorded.json()) as { node: string }).node).toBe(BUILDER_NAME);
});

withFleet("a shipment whose build server cannot build releases is refused when planned, before anything is built", async ({ page, consoleServer, problems }) => {
  // The refusal is the server's own 400, which the browser reports as a failed resource.
  problems.expect(/status of 400.*\/api\/fleet\/placements\/[^/]+\/ship/);
  await signIn(page, consoleServer, "/fleet/placements");
  const saved = await page.request.put(`/api/fleet/placements/${NODE_APP}`, {
    data: { nodes: [NODE_NAME], labels: {}, serial: 1, max_failures: null, canary: null, rollback_on_failure: true },
    headers: await csrf(page),
  });
  expect(saved.ok(), await saved.text()).toBe(true);
  await page.reload();

  await page.getByRole("button", { name: `Actions for ${NODE_APP}` }).click();
  await page.getByRole("menuitem", { name: "Build on one server, run on the others" }).click();
  const dialog = page.getByRole("dialog", { name: `Build ${NODE_APP} on one server, run it on the others` });
  await dialog.getByRole("combobox", { name: "Build it on" }).click();
  await page.getByRole("option", { name: BUILDER_NAME, exact: true }).click();
  await expect(dialog.getByRole("checkbox", { name: NODE_NAME })).toBeChecked();
  await settle(page);
  await expectNoA11yViolations(page, "the shipment form");

  // The application of the node's sandbox is in place: it has no release to build for others.
  await dialog.getByRole("button", { name: "Show the plan" }).click();
  await expect(dialog.getByText(`${NODE_APP} is not on the release layout on ${BUILDER_NAME}`)).toBeVisible();
  await settle(page);
  await expectNoA11yViolations(page, "a shipment refused at the plan");

  // Planning only plans: no shipment was queued because of it.
  const jobs = await page.request.get("/api/fleet/jobs");
  expect(jobs.ok()).toBe(true);
  const listed = (await jobs.json()) as { items?: { action?: string }[] };
  expect((listed.items ?? []).filter((job) => job.action === "placement_ship")).toHaveLength(0);
  await page.keyboard.press("Escape");
  await expect(dialog).toBeHidden();
});
