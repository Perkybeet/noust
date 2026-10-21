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

describe("the size a database's dumps may weigh", { timeout: 40_000 }, () => {
  it("is an optional limit beside the count and the days, sent as gigabytes", async () => {
    const { user, backend } = await backupsAt({ [PUT_ONE]: (call) => json(200, { ...POLICY, ...(call.body as object) }) });

    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit the backup schedule" });
    // No limit is a box that is not ticked, not an empty field: ticking it asks for the number.
    const limit = within(drawer).getByRole("checkbox", { name: "Limit their total size" });
    expect(limit).not.toBeChecked();
    expect(within(drawer).queryByLabelText("Gigabytes")).not.toBeInTheDocument();
    await user.click(limit);
    const field = within(drawer).getByLabelText("Gigabytes");
    await user.clear(field);
    await user.type(field, "2,5");
    await expectNoAxeViolations(drawer);
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));

    await waitFor(() => expect(backend.callsTo(PUT_ONE)).toHaveLength(1));
    expect(backend.callsTo(PUT_ONE)[0]?.body).toMatchObject({ retention_gb: 2.5 });
  });

  it("starts from the limit the policy has, and a destination's own limit is kept when it is saved", async () => {
    const { user, backend } = await backupsAt({
      "GET /api/databases/backup-policies/postgresql/example_production": () =>
        json(200, { ...POLICY, retention_gb: 4, destinations: [{ name: "offsite", retention_count: 3, retention_days: null, retention_gb: 20, exists: true, encrypted: true }] }),
      [PUT_ONE]: (call) => json(200, { ...POLICY, ...(call.body as object) }),
    });

    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit the backup schedule" });
    expect(within(drawer).getByRole("checkbox", { name: "Limit their total size" })).toBeChecked();
    expect(within(drawer).getByLabelText("Gigabytes")).toHaveValue("4");
    expect(within(drawer).getByLabelText("Gigabytes to keep on offsite")).toHaveValue("20");
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));

    await waitFor(() => expect(backend.callsTo(PUT_ONE)).toHaveLength(1));
    expect(backend.callsTo(PUT_ONE)[0]?.body).toMatchObject({ retention_gb: 4, destinations: [{ name: "offsite", retention_count: 3, retention_days: null, retention_gb: 20 }] });
  });

  it("refuses a size that is not a number above zero before it is sent", async () => {
    const { user, backend } = await backupsAt();

    await user.click(await screen.findByRole("button", { name: "Edit" }));
    const drawer = await screen.findByRole("dialog", { name: "Edit the backup schedule" });
    await user.click(within(drawer).getByRole("checkbox", { name: "Limit their total size" }));
    const size = within(drawer).getByLabelText("Gigabytes");
    await user.clear(size);
    await user.type(size, "-3");
    await user.click(within(drawer).getByRole("button", { name: "Save schedule" }));

    expect(await within(drawer).findByText("A number above zero, up to 100000.")).toBeInTheDocument();
    expect(backend.callsTo(PUT_ONE)).toHaveLength(0);
  });
});
