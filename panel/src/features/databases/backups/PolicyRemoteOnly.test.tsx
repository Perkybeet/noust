import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { POLICY, databaseRoutes } from "../testFixtures";

const PUT_ONE = "PUT /api/databases/backup-policies/postgresql/example_production";

async function backupsAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    databaseRoutes({
      "GET /api/backup-destinations": () =>
        json(200, { destinations: [{ name: "offsite", backend: "sftp", encrypted: true, exists: true, encryption_configured: true, settings: {} }], total: 1 }),
      "GET /api/databases/backups/remote": () => json(200, { destination: "x", engine: "postgresql", database: "example_production", dumps: [], total: 0 }),
      ...extra,
    }),
  );
  const harness = renderConsole("/databases/postgresql/example_production/backups");
  await screen.findByRole("button", { name: "Edit" });
  return { ...harness, backend };
}

describe("a policy that keeps no copy on the server", { timeout: 40_000 }, () => {
  it("is offered once a destination is ticked, and is sent as remote_only", async () => {
    const { user, backend } = await backupsAt({
      "GET /api/databases/backup-policies/postgresql/example_production": () => json(200, { ...POLICY, destinations: [] }),
      [PUT_ONE]: (call) => json(200, { ...POLICY, ...(call.body as object) }),
    });

    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit the backup schedule" });
    expect(within(drawer).queryByRole("checkbox", { name: /Keep nothing on this server/ })).not.toBeInTheDocument();
    await user.click(await within(drawer).findByRole("checkbox", { name: "offsite" }));
    const remote = within(drawer).getByRole("checkbox", { name: /Keep nothing on this server/ });
    expect(remote).not.toBeChecked();
    await user.click(remote);
    await expectNoAxeViolations(drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));

    await waitFor(() => expect(backend.callsTo(PUT_ONE)).toHaveLength(1));
    expect(backend.callsTo(PUT_ONE)[0]?.body).toMatchObject({ remote_only: true, destinations: [{ name: "offsite" }] });
  });

  it("starts from what the policy has", async () => {
    const { user } = await backupsAt({
      "GET /api/databases/backup-policies/postgresql/example_production": () =>
        json(200, { ...POLICY, remote_only: true, destinations: [{ name: "offsite", retention_count: null, retention_days: null, retention_gb: null, exists: true, encrypted: true }] }),
    });

    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit the backup schedule" });

    expect(within(drawer).getByRole("checkbox", { name: /Keep nothing on this server/ })).toBeChecked();
  });

  it("is dropped, not sent, when every destination is unticked", async () => {
    const { user, backend } = await backupsAt({
      "GET /api/databases/backup-policies/postgresql/example_production": () =>
        json(200, { ...POLICY, remote_only: true, destinations: [{ name: "offsite", retention_count: null, retention_days: null, retention_gb: null, exists: true, encrypted: true }] }),
      [PUT_ONE]: (call) => json(200, { ...POLICY, ...(call.body as object) }),
    });

    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit the backup schedule" });
    await user.click(await within(drawer).findByRole("checkbox", { name: "offsite" }));
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));

    expect(await within(drawer).findByText("Choose at least one destination to send the dumps to, or keep them here.")).toBeInTheDocument();
    expect(backend.callsTo(PUT_ONE)).toHaveLength(0);
  });
});
