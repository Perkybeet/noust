import { screen, waitFor, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { describe, expect, it } from "vitest";

import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { databaseRoutes } from "../testFixtures";

const UPLOADS = "/api/databases/imports/uploads";
const FORMAT = { kind: "sql", compression: "gzip", description: "A SQL dump, compressed with gzip.", engines: ["postgresql", "mysql"] };

function upload(received: number, size: number, extra: Record<string, unknown> = {}) {
  return {
    id: "up1",
    file_name: "shop.sql.gz",
    size,
    received,
    sha256: null,
    created_at: "2026-10-06T10:00:00+00:00",
    updated_at: "2026-10-06T10:00:00+00:00",
    complete: received >= size,
    chunk_size: 16 * 1024 ** 2,
    ...extra,
  };
}

const JOB = {
  id: "import-1",
  type: "restore",
  name: "Import into example_production",
  description: "",
  status: "completed",
  progress: 100,
  total_steps: 100,
  current_step: "",
  created_at: "2026-10-06T10:00:00",
  logs: [{ message: "Reading the dump" }, { message: "pg_restore: processing data for table public.orders" }],
  result: { engine: "postgresql", database: "example_production", created: false, replaced: false, safety_copy: "postgresql-example_production-20261006_100000.dump", notes: ["PostgreSQL owners were dropped from the dump."], format: { kind: "sql", compression: "gzip" } },
  error: null,
  metadata: { engine: "postgresql", database: "example_production" },
};

/** An in-memory upload endpoint: what the server holds, and what it is asked. */
function serverRoutes(size: number, extra: Record<string, RouteHandler> = {}) {
  const held = { received: 0 };
  const chunks: number[] = [];
  const routes: Record<string, RouteHandler> = {
    "GET /api/config": () => json(200, { config: { databases: { import_max_bytes: 2048 } }, path: "/etc/noust/config.yaml", writable: true }),
    [`POST ${UPLOADS}`]: () => json(201, upload(0, size)),
    [`PUT ${UPLOADS}/up1`]: (call) => {
      const offset = Number(call.search.get("offset"));
      if (offset !== held.received) return problem(409, "upload_offset", "Wrong offset", { hint: "Resume." });
      const bytes = call.raw instanceof Blob ? call.raw.size : 0;
      chunks.push(offset);
      held.received += bytes;
      return json(200, upload(held.received, size));
    },
    [`GET ${UPLOADS}/up1`]: () => json(200, upload(held.received, size)),
    [`POST ${UPLOADS}/up1/complete`]: () => json(200, upload(held.received, size, { format: FORMAT })),
    [`DELETE ${UPLOADS}/up1`]: () => json(200, { success: true, message: "Upload removed" }),
    "POST /api/databases/imports/inspect": () => json(200, { format: FORMAT, size, size_human: "10 B", loadable: true, reason: null }),
    // The job is answered as finished at once; the live log is the browser tests' to follow.
    "POST /api/databases/imports": () => json(202, { job_id: JOB.id, status: "completed", message: "Importing", job: JOB }),
    [`GET /api/jobs/${JOB.id}`]: () => json(200, JOB),
    ...extra,
  };
  return { routes, held, chunks };
}

async function importAt(size = 10, extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const server = serverRoutes(size, extra);
  const backend = fakeBackend(
    databaseRoutes({
      "GET /api/backup-destinations": () => json(200, { destinations: [] }),
      "GET /api/databases/backups/remote": () => json(200, { destination: "x", engine: "postgresql", database: "example_production", dumps: [], total: 0 }),
      ...server.routes,
    }),
  );
  const harness = renderConsole("/databases/postgresql/example_production/backups");
  await harness.user.click(await screen.findByRole("button", { name: "Import a dump…" }));
  const dialog = await screen.findByRole("dialog", { name: "Import a dump into example_production" });
  return { ...harness, backend, dialog, server };
}

function dump(name = "shop.sql.gz", bytes = 10): File {
  return new File([new Uint8Array(bytes)], name);
}

describe("importing a dump from the browser", () => {
  it("uploads the file, says what it is and whether the engine can load it, then loads it as a job with its output", { timeout: 30_000 }, async () => {
    const { user, dialog, backend, server } = await importAt(10);
    await user.upload(within(dialog).getByLabelText(/^Dump file/), dump());
    expect(await within(dialog).findByText("Uploaded and checked")).toBeInTheDocument();
    expect(within(dialog).getByText("A SQL script, compressed with gzip")).toBeInTheDocument();
    expect(within(dialog).getByText("A SQL dump, compressed with gzip.")).toBeInTheDocument();
    expect(await within(dialog).findByText("PostgreSQL can load it.")).toBeInTheDocument();
    expect(server.chunks).toEqual([0]);
    await expectNoAxeViolations(dialog);

    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    // The database exists: loaded over what it holds, with a copy first, until told to replace it.
    expect(await within(dialog).findByText("Loaded over the existing data")).toBeInTheDocument();
    expect(within(dialog).getByRole("checkbox", { name: /Drop and recreate it first/ })).not.toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: /Take a copy of example_production first/ })).toBeChecked();
    expect(within(dialog).getByRole("checkbox", { name: /Keep the owners and privileges/ })).not.toBeChecked();
    await user.click(within(dialog).getByRole("checkbox", { name: /Keep the owners and privileges/ }));
    await user.click(within(dialog).getByRole("button", { name: "Import" }));

    await waitFor(() => expect(backend.callsTo("POST /api/databases/imports")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/imports")[0]?.body).toEqual({
      engine: "postgresql",
      database: "example_production",
      upload_id: "up1",
      path: null,
      replace: false,
      keep_owners: true,
      safety_backup: true,
      // Loaded as the database's owner: the superuser is a decision this dialog never takes.
      as_superuser: false,
      confirm_superuser: false,
    });
    expect(await within(dialog).findByText("Imported into example_production")).toBeInTheDocument();
    // The output streams into a log of its own (its lines are drawn by the browser's layout).
    expect(within(dialog).getByLabelText("Output of the import into example_production")).toBeInTheDocument();
    expect(within(dialog).getByText("A copy of what it held is kept as postgresql-example_production-20261006_100000.dump")).toBeInTheDocument();
    expect(within(dialog).getByText("PostgreSQL owners were dropped from the dump.")).toBeInTheDocument();
  });

  it("asks to type the database's name before it is dropped, and says what is missing", { timeout: 30_000 }, async () => {
    const { user, dialog, backend } = await importAt(10);
    await user.upload(within(dialog).getByLabelText(/^Dump file/), dump());
    await within(dialog).findByText("PostgreSQL can load it.");
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    await user.click(await within(dialog).findByRole("checkbox", { name: /Drop and recreate it first/ }));
    expect(within(dialog).getByText("A copy of example_production is taken first, because it is dropped.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Import" }));
    expect(await within(dialog).findByText("Type the database's name to confirm that its data is replaced.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/databases/imports")).toHaveLength(0);
    await user.type(within(dialog).getByLabelText(/Confirm/), "example_production");
    await user.click(within(dialog).getByRole("button", { name: "Import" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/imports")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/imports")[0]?.body).toMatchObject({ replace: true });
  });

  it("refuses a file that is not a dump, or is past the size the server allows, before sending a byte", async () => {
    const { user, dialog, backend } = await importAt(10);
    const input = within(dialog).getByLabelText(/^Dump file/);
    expect(await within(dialog).findByText(/up to 2\.0 KB\./)).toBeInTheDocument();
    // The browser's own filter (the input's accept) is not what is under test.
    await userEvent.setup({ applyAccept: false }).upload(input, dump("notes.txt"));
    expect(await within(dialog).findByText(/notes\.txt does not look like a database dump/)).toBeInTheDocument();
    await user.upload(input, dump("huge.sql", 4000));
    expect(await within(dialog).findByText("huge.sql is 3.9 KB, and this server takes up to 2.0 KB by upload.")).toBeInTheDocument();
    expect(within(dialog).getByText(/Raise databases\.import_max_bytes/)).toBeInTheDocument();
    expect(backend.callsTo(`POST ${UPLOADS}`)).toHaveLength(0);
  });

  it("says why an engine cannot load the file, and does not go on", async () => {
    const { user, dialog } = await importAt(10, {
      "POST /api/databases/imports/inspect": () =>
        json(200, { format: { ...FORMAT, kind: "mongo-archive", engines: ["mongodb"] }, size: 10, size_human: "10 B", loadable: false, reason: "A MongoDB archive cannot be loaded into PostgreSQL." }),
    });
    await user.upload(within(dialog).getByLabelText(/^Dump file/), dump());
    expect(await within(dialog).findByText("PostgreSQL cannot load it.")).toBeInTheDocument();
    expect(within(dialog).getByText("A MongoDB archive cannot be loaded into PostgreSQL.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    expect(await within(dialog).findAllByText("PostgreSQL cannot load it.")).not.toHaveLength(0);
    expect(within(dialog).queryByRole("checkbox", { name: /Drop and recreate/ })).not.toBeInTheDocument();
  });

  it("goes on by itself when the connection drops, and says so while it waits", { timeout: 30_000 }, async () => {
    let failed = false;
    const { user, dialog, server } = await importAt(10, {
      [`PUT ${UPLOADS}/up1`]: () => {
        if (!failed) {
          failed = true;
          throw new TypeError("Failed to fetch");
        }
        server.held.received = 10;
        return json(200, upload(10, 10));
      },
    });
    await user.upload(within(dialog).getByLabelText(/^Dump file/), dump());
    expect(await within(dialog).findByText(/The connection dropped\. Trying again/)).toBeInTheDocument();
    expect(await within(dialog).findByText("Uploaded and checked", {}, { timeout: 10_000 })).toBeInTheDocument();
  });

  it("cancels an upload and has the server throw it away", { timeout: 30_000 }, async () => {
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
      release = resolve;
    });
    const { user, dialog, backend } = await importAt(10, {
      [`PUT ${UPLOADS}/up1`]: async () => {
        await gate;
        return json(200, upload(10, 10));
      },
    });
    await user.upload(within(dialog).getByLabelText(/^Dump file/), dump());
    await user.click(await within(dialog).findByRole("button", { name: "Cancel upload" }));
    await waitFor(() => expect(backend.callsTo(`DELETE ${UPLOADS}/up1`)).toHaveLength(1));
    expect(await within(dialog).findByText("Upload cancelled. The server threw away what it had.")).toBeInTheDocument();
    release();
  });

  it("shows the server's refusal of an upload, in its words", async () => {
    const { user, dialog } = await importAt(10, {
      [`POST ${UPLOADS}`]: () => problem(400, "validation_error", "Not enough space for the upload: it is 10 bytes and /var/backups has 4 bytes free", { hint: "Free space on that disk first; nothing was received." }),
    });
    await user.upload(within(dialog).getByLabelText(/^Dump file/), dump());
    expect(await within(dialog).findByText("The upload failed")).toBeInTheDocument();
    expect(within(dialog).getByText("Not enough space for the upload: it is 10 bytes and /var/backups has 4 bytes free")).toBeInTheDocument();
    expect(within(dialog).getByText("Free space on that disk first; nothing was received.")).toBeInTheDocument();
  });

  it("is started from the databases list too, with the engine and the database chosen", { timeout: 30_000 }, async () => {
    screenWidth(1440);
    const server = serverRoutes(10);
    const backend = fakeBackend(databaseRoutes({ ...server.routes }));
    const { user } = renderConsole("/databases");
    await user.click(await screen.findByRole("button", { name: "Import a dump…" }));
    const dialog = await screen.findByRole("dialog", { name: "Import a dump" });
    await user.click(await within(dialog).findByRole("combobox", { name: "Engine" }));
    await user.click(await screen.findByRole("option", { name: /^PostgreSQL/ }));
    await user.type(within(dialog).getByLabelText(/^Database/), "from_elsewhere");
    await user.upload(within(dialog).getByLabelText(/^Dump file/), dump());
    await within(dialog).findByText("Uploaded and checked");
    await user.click(within(dialog).getByRole("button", { name: "Continue" }));
    expect(await within(dialog).findByText("A new database is created")).toBeInTheDocument();
    expect(within(dialog).queryByRole("checkbox", { name: /Drop and recreate it first/ })).not.toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Import" }));
    await waitFor(() => expect(backend.callsTo("POST /api/databases/imports")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/imports")[0]?.body).toMatchObject({ engine: "postgresql", database: "from_elsewhere", replace: false });
  });
});
