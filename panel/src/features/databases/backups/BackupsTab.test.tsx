import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { databaseRoutes } from "../testFixtures";

const JOB = { id: "restore-1", type: "restore", name: "Restore", description: "", status: "running", progress: 10, total_steps: 100, current_step: "", created_at: "2026-09-29T10:00:00", logs: [{ message: "pg_restore: processing data for table public.orders" }], metadata: { engine: "postgresql", database: "example_production" } };

async function backupsAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    databaseRoutes({
      "GET /api/backup-destinations": () => json(200, { destinations: [{ name: "offsite-sftp", type: "sftp", encrypted: false }] }),
      "GET /api/databases/backups/remote": () => json(200, { destination: "offsite-sftp", engine: "postgresql", database: "example_production", dumps: [], total: 0 }),
      "GET /api/databases/backups/suggest-name": () => json(200, { name: "example_production_restored" }),
      "GET /api/jobs/restore-1": () => json(200, JOB),
      ...extra,
    }),
  );
  const harness = renderConsole("/databases/postgresql/example_production/backups");
  await screen.findByRole("heading", { level: 2, name: "Schedule" });
  return { ...harness, backend };
}

describe("a database's backups", () => {
  it("says the schedule in plain words, what it keeps and where it sends, then every dump with its check", async () => {
    await backupsAt();
    expect(await screen.findByText("Every day at 02:00")).toBeInTheDocument();
    expect(screen.getByText("The last 7 dumps, for up to 30 days")).toBeInTheDocument();
    const dumps = await screen.findByRole("region", { name: "Dumps of example_production" });
    expect(within(dumps).getByText("Verified")).toBeInTheDocument();
    expect(within(dumps).getByText("Check failed")).toBeInTheDocument();
    expect(within(dumps).getByText("offsite-sftp")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("restores into a new database by default, and follows the job in the page's slot", async () => {
    const { user, backend } = await backupsAt({
      "POST /api/databases/backups/restore": () => json(202, { job_id: JOB.id, status: "pending", message: "Restoring", job: JOB }),
    });
    const dumps = await screen.findByRole("region", { name: "Dumps of example_production" });
    await user.click(within(dumps).getByRole("button", { name: "Actions for postgresql-example_production-20260929_020000.dump" }));
    await user.click(await screen.findByRole("menuitem", { name: "Restore…" }));
    const dialog = await screen.findByRole("dialog", { name: "Restore a dump" });
    await waitFor(() => expect(within(dialog).getByLabelText("New database name")).toHaveValue("example_production_restored"));
    await user.click(within(dialog).getByRole("button", { name: "Restore as new database" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/backups/restore")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/backups/restore")[0]?.body).toEqual({
      engine: "postgresql",
      database: "example_production",
      backup_name: "postgresql-example_production-20260929_020000.dump",
      drop_existing: false,
      safety_backup: true,
      new_name: "example_production_restored",
    });
    expect(await screen.findByText("Restoring into example_production_restored")).toBeInTheDocument();
    expect(screen.getByText("pg_restore: processing data for table public.orders")).toBeInTheDocument();
  });

  it("asks for the name before restoring over the database, with its safety copy said", async () => {
    const { user } = await backupsAt();
    const dumps = await screen.findByRole("region", { name: "Dumps of example_production" });
    await user.click(within(dumps).getByRole("button", { name: "Actions for postgresql-example_production-20260929_020000.dump" }));
    await user.click(await screen.findByRole("menuitem", { name: "Restore…" }));
    const dialog = await screen.findByRole("dialog", { name: "Restore a dump" });
    await user.click(within(dialog).getByRole("combobox", { name: "Restore into" }));
    await user.click(await screen.findByRole("option", { name: /example_production, replacing/ }));
    expect(within(dialog).getByText(/A safety copy of example_production is taken first/)).toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox", { name: /Drop and recreate it first/ })).not.toBeChecked();
    const submit = within(dialog).getByRole("button", { name: "Restore over example_production" });
    expect(submit).toBeDisabled();
    await user.type(within(dialog).getByLabelText(/Type example_production to confirm/), "example_production");
    expect(submit).toBeEnabled();
  });
});
