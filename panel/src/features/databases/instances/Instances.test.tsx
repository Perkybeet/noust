import { act, screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json, problem } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { screenWidth } from "../../app/testRoutes";
import { databaseRoutes } from "../testFixtures";

const DIGEST = `sha256:${"a".repeat(64)}`;

const INSTANCE = {
  name: "pg16",
  key: "postgresql@pg16",
  engine: "postgresql",
  flavour: "postgresql",
  display_name: "PostgreSQL",
  version: "16",
  image: "postgres",
  digest: DIGEST,
  previous_digest: null,
  reference: `postgres:16@${DIGEST}`,
  host_port: 5433,
  address: "127.0.0.1",
  memory_limit_mb: 512,
  state: "ready",
  running: true,
  status: "running",
  unit: "noust-db-pg16",
  container: "noust-db-pg16",
  volume: "noust-db-pg16-data",
  directory: "/var/lib/noust/db-instances/pg16",
  created_at: "2026-10-01T10:00:00+00:00",
  created_by: null,
  updated_at: "2026-10-01T10:00:00+00:00",
  upgraded_at: null,
  end_of_life: null,
  support: null,
  tls_mode: "off",
};

const BUDGET = { server_mb: 8192, allocatable_mb: 7372, allocated_mb: 512, available_mb: 6860 };

const CATALOG = {
  engines: [
    {
      flavour: "postgresql",
      engine: "postgresql",
      display_name: "PostgreSQL",
      image: "postgres",
      container_port: 5432,
      min_memory_mb: 256,
      default_memory_mb: 512,
      versions: ["15", "16", "17", "18"].map((version) => ({ version, digest: DIGEST, default: version === "18", end_of_life: null })),
    },
    {
      flavour: "redis",
      engine: "redis",
      display_name: "Redis",
      image: "redis",
      container_port: 6379,
      min_memory_mb: 64,
      default_memory_mb: 256,
      versions: [{ version: "8.0", digest: DIGEST, default: true, end_of_life: null }],
    },
  ],
};

const PLAN = {
  source: "postgresql@pg16",
  instance: "pg16",
  flavour: "postgresql",
  display_name: "PostgreSQL",
  from_version: "16",
  to_version: "18",
  target: "postgresql18",
  new_instance: {
    name: "postgresql18",
    key: "postgresql@postgresql18",
    flavour: "postgresql",
    engine: "postgresql",
    display_name: "PostgreSQL",
    version: "18",
    image: "postgres",
    digest: DIGEST,
    reference: `postgres:18@${DIGEST}`,
    host_port: 5434,
    memory_limit_mb: 512,
    warnings: [],
  },
  databases: [{ source: "postgresql@pg16", target: "postgresql18", database: "shop_db", engine: "postgresql", apps: ["shop.example.com"], accounts: ["shop_user"], new_passwords: [], policies: [], blockers: [], warnings: [] }],
  apps: ["shop.example.com"],
  blockers: [],
  warnings: ["Each database is dumped and loaded again: writes made to it while that runs are not carried over."],
};

const MIGRATION = {
  id: 3,
  source: "postgresql",
  target: "postgresql17",
  flavour: "postgresql",
  from_version: "16.4",
  to_version: "17",
  state: "done",
  moved: [{ database: "shop_db", apps: ["shop.example.com"] }],
  created_at: "2026-10-05T10:00:00+00:00",
  created_by: null,
  finished_at: "2026-10-05T10:05:00+00:00",
  can_roll_back: true,
};

const CA_PATH = "/etc/noust/tls/ca.crt";

const TLS_OFF = {
  name: "pg16",
  key: "postgresql@pg16",
  mode: "off",
  certificate: null,
  ca: null,
  supported: { off: null, optional: null, required: null },
};

const CERTIFICATE = {
  path: "/var/lib/noust/db-instances/pg16/tls/server.crt",
  subject: "CN = pg16",
  issuer: "CN = Noust database CA",
  names: ["localhost", "127.0.0.1", "pg16"],
  not_before: "2026-10-01T10:00:00+00:00",
  not_after: "2028-12-30T10:00:00+00:00",
  days_left: 810,
  expiring: false,
  fingerprint: "AB:CD:EF:01",
};

const TLS_ON = {
  ...TLS_OFF,
  mode: "optional",
  certificate: CERTIFICATE,
  ca: { ...CERTIFICATE, path: CA_PATH, subject: "CN = Noust database CA", names: [] },
};

const TLS_PLAN = {
  name: "pg16",
  mode: "required",
  current: "off",
  restarts: true,
  apps: ["shop.example.com"],
  names: ["localhost", "127.0.0.1", "pg16"],
  blockers: [],
  warnings: ["pg16 restarts: its connections drop for a few seconds, shop.example.com among them"],
};

const JOB = { id: "j1", type: "database", name: "Job", description: "", status: "running", progress: 10, total_steps: 100, current_step: "Working", created_at: "2026-10-06T10:00:00", logs: [], metadata: {} };
const ACCEPTED = { job_id: "j1", status: "pending", message: "queued", job: JOB };

async function enginesAt(extra: Record<string, RouteHandler> = {}) {
  screenWidth(1440);
  const backend = fakeBackend(
    databaseRoutes({
      "GET /api/databases/instances": () => json(200, { instances: [INSTANCE], budget: BUDGET }),
      "GET /api/databases/instances/catalog": () => json(200, CATALOG),
      "GET /api/databases/instances/migrations": () => json(200, { migrations: [] }),
      "GET /api/jobs/j1": () => json(200, JOB),
      ...extra,
    }),
  );
  const harness = renderConsole("/databases/engines");
  const section = await screen.findByRole("region", { name: "Instances Noust runs" }, { timeout: 15_000 });
  // The rows arrive after the section: wait for the instance and for the server's own engines.
  await within(section).findByText("pg16");
  await screen.findAllByText("PostgreSQL");
  return { harness, backend };
}

/** The row a cell is in. */
function rowOf(cell: HTMLElement): HTMLElement {
  const row = cell.closest<HTMLElement>("tr");
  if (row === null) throw new Error("no row around the cell");
  return row;
}

async function menuOf(user: ReturnType<typeof renderConsole>["user"], name: string) {
  await user.click(await screen.findByRole("button", { name: `Actions for the instance ${name}` }));
}

describe("the instances Noust runs", { timeout: 60_000 }, () => {
  it("lists each with where it listens, its memory, and how much of the server's memory the limits take", async () => {
    await enginesAt();

    const table = screen.getByRole("region", { name: "Instances Noust runs" });
    const row = rowOf(within(table).getByText("pg16"));
    expect(within(row).getByText("Running")).toBeInTheDocument();
    expect(within(row).getByText("127.0.0.1:5433")).toBeInTheDocument();
    expect(within(row).getByText("512 MB")).toBeInTheDocument();
    expect(within(row).getByText("postgresql@pg16")).toBeInTheDocument();
    expect(within(table).getByText("Memory limits add up to 512 MB of the 7372 MB instances may hold (6860 MB left).")).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("is not shown by a server that has none of this", async () => {
    screenWidth(1440);
    fakeBackend(databaseRoutes());
    renderConsole("/databases/engines");
    await screen.findAllByText("PostgreSQL", undefined, { timeout: 15_000 });

    expect(screen.queryByRole("region", { name: "Instances Noust runs" })).not.toBeInTheDocument();
  });

  it("makes an instance from the catalog and follows the job", async () => {
    const { backend, harness } = await enginesAt({ "POST /api/databases/instances": () => json(202, ACCEPTED) });
    const { user } = harness;

    await user.click(screen.getByRole("button", { name: "New instance" }));
    const dialog = await screen.findByRole("dialog", { name: "New instance" });
    await user.click(await within(dialog).findByRole("combobox", { name: "Engine" }));
    await user.click(await screen.findByRole("option", { name: "PostgreSQL" }));
    await user.type(within(dialog).getByLabelText("Name"), "pg18");
    await user.type(within(dialog).getByLabelText(/^Memory limit/), "1024");
    await expectNoAxeViolations(dialog);
    await user.click(within(dialog).getByRole("button", { name: "Make the instance" }));

    await waitFor(() => expect(backend.callsTo("POST /api/databases/instances")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/instances")[0]?.body).toEqual({ name: "pg18", engine: "postgresql", version: "18", memory_mb: 1024 });
    expect(await screen.findByText("Making the instance pg18")).toBeInTheDocument();
  });

  it("says a name or a memory limit the server would refuse before sending anything", async () => {
    const { backend, harness } = await enginesAt();
    const { user } = harness;

    await user.click(screen.getByRole("button", { name: "New instance" }));
    const dialog = await screen.findByRole("dialog", { name: "New instance" });
    await user.click(await within(dialog).findByRole("combobox", { name: "Engine" }));
    await user.click(await screen.findByRole("option", { name: "PostgreSQL" }));
    await user.type(within(dialog).getByLabelText("Name"), "Bad Name");
    await user.type(within(dialog).getByLabelText(/^Memory limit/), "100");
    await user.click(within(dialog).getByRole("button", { name: "Make the instance" }));

    expect(await within(dialog).findByText(/Use lower-case letters, digits and single dashes/)).toBeInTheDocument();
    expect(within(dialog).getByText("This engine needs at least 256 MB.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/databases/instances")).toHaveLength(0);
  });

  it("shows the server's refusal of a request that could not succeed, as it said it", async () => {
    const { harness } = await enginesAt({
      "POST /api/databases/instances": () => problem(400, "validationerror", "Docker is not installed", { hint: "Install docker.io with its Compose plugin." }),
    });
    const { user } = harness;

    await user.click(screen.getByRole("button", { name: "New instance" }));
    const dialog = await screen.findByRole("dialog", { name: "New instance" });
    await user.click(await within(dialog).findByRole("combobox", { name: "Engine" }));
    await user.click(await screen.findByRole("option", { name: "PostgreSQL" }));
    await user.type(within(dialog).getByLabelText("Name"), "pg18");
    await user.type(within(dialog).getByLabelText(/^Memory limit/), "1024");
    await user.click(within(dialog).getByRole("button", { name: "Make the instance" }));

    expect(await within(dialog).findByText("Docker is not installed")).toBeInTheDocument();
    expect(within(dialog).getByText("Install docker.io with its Compose plugin.")).toBeInTheDocument();
  });

  it("removes one after its name is typed, and says its data goes", async () => {
    const { backend, harness } = await enginesAt({ "DELETE /api/databases/instances/pg16": () => json(202, ACCEPTED) });
    const { user } = harness;

    await menuOf(user, "pg16");
    await user.click(await screen.findByRole("menuitem", { name: "Remove…" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Remove pg16?" });
    expect(within(dialog).getByText(/deleted for good/)).toBeInTheDocument();
    await user.type(within(dialog).getByRole("textbox"), "pg16");
    await user.click(within(dialog).getByRole("button", { name: "Remove the instance" }));

    await waitFor(() => expect(backend.callsTo("DELETE /api/databases/instances/pg16")).toHaveLength(1));
    expect(backend.callsTo("DELETE /api/databases/instances/pg16")[0]?.search.get("confirm")).toBe("true");
  });
});

describe("moving an engine to a newer version", { timeout: 60_000 }, () => {
  it("offers only the versions newer than the one the instance runs, and shows what the move would do", async () => {
    const { harness } = await enginesAt({ "POST /api/databases/instances/migrate/plan": () => json(200, PLAN) });
    const { user } = harness;

    await menuOf(user, "pg16");
    await user.click(await screen.findByRole("menuitem", { name: "Move to a newer version…" }));
    const dialog = await screen.findByRole("dialog", { name: /Move PostgreSQL 16 \(pg16\) to a newer version/ });
    await user.click(within(dialog).getByRole("combobox", { name: "Version to go to" }));
    const options = (await screen.findAllByRole("option")).map((option) => option.textContent);
    expect(options).toEqual(["17", "18"]);
    await user.keyboard("{Escape}");

    expect(await within(dialog).findByText("postgresql18")).toBeInTheDocument();
    expect(within(dialog).getByText("shop_db")).toBeInTheDocument();
    expect(within(dialog).getByText("shop.example.com")).toBeInTheDocument();
    expect(within(dialog).getByText(/writes made to it while that runs are not carried over/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Move it" })).toBeEnabled();
    await expectNoAxeViolations(dialog);
  });

  it("says what stops it and does not offer to go on", async () => {
    const { harness } = await enginesAt({
      "POST /api/databases/instances/migrate/plan": () => json(200, { ...PLAN, blockers: ["shop_db: shop.example.com owns it without a link Noust wrote"] }),
    });
    const { user } = harness;

    await menuOf(user, "pg16");
    await user.click(await screen.findByRole("menuitem", { name: "Move to a newer version…" }));
    const dialog = await screen.findByRole("dialog", { name: /to a newer version/ });

    expect(await within(dialog).findByText("This move cannot go ahead")).toBeInTheDocument();
    expect(within(dialog).getByText("shop_db: shop.example.com owns it without a link Noust wrote")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Move it" })).toBeDisabled();
  });

  it("queues the move with the version chosen and follows the job", async () => {
    const { backend, harness } = await enginesAt({
      "POST /api/databases/instances/migrate/plan": () => json(200, PLAN),
      "POST /api/databases/instances/migrate": () => json(202, ACCEPTED),
    });
    const { user } = harness;

    await menuOf(user, "pg16");
    await user.click(await screen.findByRole("menuitem", { name: "Move to a newer version…" }));
    const dialog = await screen.findByRole("dialog", { name: /to a newer version/ });
    await within(dialog).findByText("postgresql18");
    await user.click(within(dialog).getByRole("button", { name: "Move it" }));

    await waitFor(() => expect(backend.callsTo("POST /api/databases/instances/migrate")).toHaveLength(1));
    expect(backend.callsTo("POST /api/databases/instances/migrate")[0]?.body).toEqual({ source: "pg16", to_version: "18" });
    expect(await screen.findByText(/Moving PostgreSQL 16 \(pg16\) to a newer version/)).toBeInTheDocument();
  });

  it("is also offered for the server's own PostgreSQL, which it leaves as it is", async () => {
    const { backend, harness } = await enginesAt({ "POST /api/databases/instances/migrate/plan": () => json(200, { ...PLAN, source: "postgresql", instance: null, from_version: "16.4" }) });
    const { user } = harness;

    const own = screen.getByRole("region", { name: "Database engines" });
    const row = rowOf(within(own).getByText("PostgreSQL"));
    await user.click(within(row).getByRole("button", { name: /Actions for/ }));
    await user.click(await screen.findByRole("menuitem", { name: "Move to a newer version…" }));
    const dialog = await screen.findByRole("dialog", { name: /Move PostgreSQL to a newer version/ });
    await within(dialog).findByText("postgresql18");

    expect(backend.callsTo("POST /api/databases/instances/migrate/plan")[0]?.body).toMatchObject({ source: "postgresql", to_version: "18" });
  });

  it("lists the moves and rolls one back after asking, saying what is not carried back", async () => {
    const { backend, harness } = await enginesAt({
      "GET /api/databases/instances/migrations": () => json(200, { migrations: [MIGRATION] }),
      "POST /api/databases/instances/migrations/3/rollback": () => json(202, ACCEPTED),
    });
    const { user } = harness;

    const moves = await screen.findByRole("group", { name: "Moves to a newer version" });
    expect(within(moves).getByText("postgresql17")).toBeInTheDocument();
    expect(within(moves).getByText("Done")).toBeInTheDocument();
    await user.click(within(moves).getByRole("button", { name: "Actions for the move 3" }));
    await user.click(await screen.findByRole("menuitem", { name: "Roll back…" }));
    const ask = await screen.findByRole("alertdialog", { name: "Roll back move 3?" });
    expect(within(ask).getByText(/not carried back/)).toBeInTheDocument();
    await user.click(within(ask).getByRole("button", { name: "Roll back" }));

    await waitFor(() => expect(backend.callsTo("POST /api/databases/instances/migrations/3/rollback")).toHaveLength(1));
  });

  it("speaks Spanish", async () => {
    await enginesAt();

    await act(() => setLocale("es"));

    expect(await screen.findByRole("region", { name: "Instancias que ejecuta Noust" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nueva instancia" })).toBeInTheDocument();
  });
});

describe("TLS of an instance", { timeout: 60_000 }, () => {
  it("shows the mode in the list, and says in the dialog what the engine could serve and what it could not", async () => {
    const { harness } = await enginesAt({
      "GET /api/databases/instances/pg16/tls": () => json(200, { ...TLS_OFF, supported: { off: null, optional: null, required: "MariaDB 10.11 cannot refuse a plaintext connection (it can from 11.4)" } }),
    });
    const { user } = harness;

    const row = rowOf(within(screen.getByRole("region", { name: "Instances Noust runs" })).getByText("pg16"));
    expect(within(row).getByText("Off")).toBeInTheDocument();
    await menuOf(user, "pg16");
    await user.click(await screen.findByRole("menuitem", { name: "TLS…" }));
    const dialog = await screen.findByRole("dialog", { name: "TLS of pg16" });

    expect(await within(dialog).findByText("No certificate: the instance serves no TLS.")).toBeInTheDocument();
    expect(within(dialog).getByText(/cannot refuse a plaintext connection/)).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Apply" })).toBeDisabled();
    expect(within(dialog).queryByRole("button", { name: "Renew certificate" })).not.toBeInTheDocument();
    await expectNoAxeViolations(dialog);
  });

  it("shows what a mode would do before it is applied, and queues it with that mode", async () => {
    const { backend, harness } = await enginesAt({
      "GET /api/databases/instances/pg16/tls": () => json(200, TLS_OFF),
      "POST /api/databases/instances/pg16/tls/plan": () => json(200, TLS_PLAN),
      "PUT /api/databases/instances/pg16/tls": () => json(202, ACCEPTED),
    });
    const { user } = harness;

    await menuOf(user, "pg16");
    await user.click(await screen.findByRole("menuitem", { name: "TLS…" }));
    const dialog = await screen.findByRole("dialog", { name: "TLS of pg16" });
    await user.click(await within(dialog).findByRole("combobox", { name: "Mode" }));
    await user.click(await screen.findByRole("option", { name: /Required/ }));

    expect(await within(dialog).findByText("shop.example.com")).toBeInTheDocument();
    expect(within(dialog).getByText(/connections drop for a few seconds/)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/databases/instances/pg16/tls/plan")[0]?.body).toEqual({ mode: "required" });
    await user.click(within(dialog).getByRole("button", { name: "Apply" }));

    await waitFor(() => expect(backend.callsTo("PUT /api/databases/instances/pg16/tls")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/databases/instances/pg16/tls")[0]?.body).toEqual({ mode: "required" });
    expect(await screen.findByText("Setting pg16 to TLS Required")).toBeInTheDocument();
  });

  it("does not offer to go on when the plan has a blocker", async () => {
    const { harness } = await enginesAt({
      "GET /api/databases/instances/pg16/tls": () => json(200, TLS_OFF),
      "POST /api/databases/instances/pg16/tls/plan": () => json(200, { ...TLS_PLAN, blockers: ["pg16 is not ready: it is upgrading"] }),
    });
    const { user } = harness;

    await menuOf(user, "pg16");
    await user.click(await screen.findByRole("menuitem", { name: "TLS…" }));
    const dialog = await screen.findByRole("dialog", { name: "TLS of pg16" });
    await user.click(await within(dialog).findByRole("combobox", { name: "Mode" }));
    await user.click(await screen.findByRole("option", { name: /Optional/ }));

    expect(await within(dialog).findByText("pg16 is not ready: it is upgrading")).toBeInTheDocument();
    expect(within(dialog).getByRole("button", { name: "Apply" })).toBeDisabled();
  });

  it("shows the certificate and the authority, and renews it after asking", async () => {
    const { backend, harness } = await enginesAt({
      "GET /api/databases/instances": () => json(200, { instances: [{ ...INSTANCE, tls_mode: "optional" }], budget: BUDGET }),
      "GET /api/databases/instances/pg16/tls": () => json(200, TLS_ON),
      "POST /api/databases/instances/pg16/tls/renew": () => json(202, ACCEPTED),
    });
    const { user } = harness;

    await menuOf(user, "pg16");
    await user.click(await screen.findByRole("menuitem", { name: "TLS…" }));
    const dialog = await screen.findByRole("dialog", { name: "TLS of pg16" });

    expect(await within(dialog).findByText("localhost, 127.0.0.1, pg16")).toBeInTheDocument();
    expect(within(dialog).getByText("AB:CD:EF:01")).toBeInTheDocument();
    expect(within(dialog).getByText(CA_PATH)).toBeInTheDocument();
    expect(within(dialog).getByText("810 days left")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Renew certificate" }));
    const ask = await screen.findByRole("alertdialog", { name: "Sign the certificate of pg16 again?" });
    await user.click(within(ask).getByRole("button", { name: "Renew it" }));

    await waitFor(() => expect(backend.callsTo("POST /api/databases/instances/pg16/tls/renew")).toHaveLength(1));
    expect(await screen.findByText("Signing a new certificate for pg16")).toBeInTheDocument();
  });

  it("says when the certificate is about to expire", async () => {
    const { harness } = await enginesAt({
      "GET /api/databases/instances": () => json(200, { instances: [{ ...INSTANCE, tls_mode: "optional" }], budget: BUDGET }),
      "GET /api/databases/instances/pg16/tls": () => json(200, { ...TLS_ON, certificate: { ...CERTIFICATE, days_left: 12, expiring: true } }),
    });
    const { user } = harness;

    await menuOf(user, "pg16");
    await user.click(await screen.findByRole("menuitem", { name: "TLS…" }));
    const dialog = await screen.findByRole("dialog", { name: "TLS of pg16" });

    expect(await within(dialog).findByText("It expires soon. Renew it.")).toBeInTheDocument();
  });
});
