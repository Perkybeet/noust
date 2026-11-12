/**
 * Tasks that run once and retire themselves: the choice in the dialog (only on a server that can do it), the moment in
 * the server's time with its zone said, a moment that has passed refused with the server's own words, the list saying a
 * task is a one-time one, and the record of the ones that ran with "Schedule again" and what each printed.
 */

import { act, fireEvent, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, onNode, problem, signedInRoutes } from "../../test/fakes";
import type { RecordedCall, RouteHandler } from "../../test/fakes";
import { WEB2, centralSession, fleetRoutes } from "../fleet/testFixtures";
import { findLoadedRegion } from "../../test/regions";

beforeEach(() => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: true,
    media: query,
    onchange: null,
    addEventListener: () => undefined,
    removeEventListener: () => undefined,
    addListener: () => undefined,
    removeListener: () => undefined,
    dispatchEvent: () => false,
  }));
});

const PASSED = "2020-01-01 02:00 has already passed on this server";
const PASSED_HINT = "It is 2026-11-12 10:00 here (UTC+01:00). Choose a later moment.";

/** The preview of a moment, as the server answers it: the moment with the offset of its own clock, or why not. */
function previewRoute(call: RecordedCall) {
  const body = call.body as { schedule?: string; once_at?: string } | undefined;
  const onceAt = body?.once_at;
  if (onceAt === undefined) return json(200, { calendar: "*-*-* 02:00:00", next_runs: ["2026-09-27T02:00:00+00:00"] });
  if (onceAt.startsWith("2020")) return problem(400, "validationerror", PASSED, { fields: { once_at: PASSED }, hint: PASSED_HINT });
  const [date = "", time = ""] = onceAt.split(" ");
  return json(200, { calendar: `${date} ${time}:00`, next_runs: [`${date}T${time}:00+01:00`] });
}

const REPEATING = {
  name: "nightly-backup",
  command: "noust backup create shop.example.com",
  user: "shop",
  working_directory: "/var/www/shop",
  app_domain: "shop.example.com",
  schedule: "daily",
  on_calendar: "*-*-* 02:00:00",
  enabled: true,
  next_run: "Fri 2026-09-26 02:00:00 UTC",
  last_run: "Thu 2026-09-25 02:00:00 UTC",
  last_exit_code: 0,
  last_result: "success",
  once: false,
  once_at: "",
};

const ONCE_TASK = {
  ...REPEATING,
  name: "reindex",
  command: "php artisan search:reindex",
  schedule: "once",
  on_calendar: "2026-12-01 14:30:00",
  once: true,
  once_at: "2026-12-01T14:30:00+01:00",
  last_run: "never",
  last_exit_code: null,
  last_result: "never ran",
};

const RAN = [
  {
    id: 2,
    name: "cleanup",
    command: "/usr/bin/cleanup --all",
    app_domain: "",
    run_as: "root",
    working_directory: "",
    timeout: 600,
    service: "",
    run_at: "2026-11-10T03:00:00+01:00",
    started_at: "2026-11-10T02:20:00+00:00",
    finished_at: "2026-11-10T02:21:00+00:00",
    result: "exit-code",
    exit_code: 2,
    late: true,
    output: "ERROR: disk full\n",
    retired_at: "2026-11-10T02:21:01+00:00",
    ok: false,
  },
  {
    id: 1,
    name: "reindex-shop",
    command: "php artisan search:reindex",
    app_domain: "shop.example.com",
    run_as: "noust-app-shop",
    working_directory: "/var/www/apps/shop/current",
    timeout: null,
    service: "",
    run_at: "2026-11-09T14:30:00+01:00",
    started_at: "2026-11-09T13:30:01+00:00",
    finished_at: "2026-11-09T13:31:10+00:00",
    result: "success",
    exit_code: 0,
    late: false,
    output: "",
    retired_at: "2026-11-09T13:31:11+00:00",
    ok: true,
  },
];

const HISTORY = { items: RAN, total: 2, days: 30, zone: "UTC+01:00" };

async function cronAt(jobs: unknown[] = [REPEATING], extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    "GET /api/cron": () => json(200, { jobs, total: jobs.length }),
    "GET /api/cron/history": () => json(200, { items: [], total: 0, days: 30, zone: "UTC+01:00" }),
    "POST /api/cron/preview": previewRoute,
    "POST /api/cron": () => json(201, { success: true, message: "created", job: ONCE_TASK }),
    ...extra,
  });
  const harness = renderConsole("/cron");
  await screen.findByRole("heading", { level: 1, name: "Scheduled tasks" });
  const table = await findLoadedRegion(/Scheduled tasks/);
  return { ...harness, backend, table };
}

describe("the choice between repeating and running once", () => {
  it("is in the dialog on a server that can run a task once, and a task made once goes to the server with its moment", async () => {
    const { user, backend } = await cronAt();
    await user.click(screen.getByRole("button", { name: "New task" }));
    const dialog = await screen.findByRole("dialog", { name: "New scheduled task" });
    await user.type(within(dialog).getByLabelText("Name"), "reindex");
    await user.type(within(dialog).getByLabelText("Command"), "php artisan search:reindex");

    await user.click(within(dialog).getByRole("radio", { name: "Once" }));
    fireEvent.change(within(dialog).getByLabelText("Date"), { target: { value: "2026-12-01" } });
    const time = within(dialog).getByLabelText("At");
    await user.clear(time);
    await user.type(time, "14:30");

    // The preview says it is once, and on whose clock: the server's, with its zone.
    expect(await within(dialog).findByText("Runs once, then retires itself")).toBeInTheDocument();
    expect(within(dialog).getByText(/On the server's clock \(UTC\+01:00\)/)).toBeInTheDocument();
    expect(within(dialog).getByText("The time is the server's. The task retires itself once it has run.")).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Create task" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/cron")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/cron")[0]?.body).toMatchObject({ name: "reindex", once_at: "2026-12-01 14:30" });
  });

  it("is not in the dialog of a server whose Noust has no such task, so a task that repeats is never made by mistake", async () => {
    fakeBackend({
      ...fleetRoutes(centralSession(), [WEB2]),
      ...onNode("web-2", {
        "GET /api/openapi.json": () => json(200, { paths: { "/api/cron": { get: {}, post: {} }, "/api/cron/preview": { post: {} } } }),
        "GET /api/cron": () => json(200, { jobs: [REPEATING], total: 1 }),
        "GET /api/apps": () => json(200, { apps: [], total: 0 }),
        "POST /api/cron/preview": previewRoute,
      }),
    });
    const { user } = renderConsole("/n/web-2/cron");

    await user.click(await screen.findByRole("button", { name: "New task" }));
    const dialog = await screen.findByRole("dialog", { name: "New scheduled task" });

    expect(within(dialog).getByLabelText("Name")).toBeInTheDocument();
    expect(within(dialog).queryByRole("radio", { name: "Once" })).toBeNull();
  });

  it("says a moment that has passed with the server's own words, where it is chosen, and creates nothing", async () => {
    const { user, backend } = await cronAt();
    await user.click(screen.getByRole("button", { name: "New task" }));
    const dialog = await screen.findByRole("dialog", { name: "New scheduled task" });
    await user.type(within(dialog).getByLabelText("Name"), "reindex");
    await user.type(within(dialog).getByLabelText("Command"), "true");
    await user.click(within(dialog).getByRole("radio", { name: "Once" }));

    fireEvent.change(within(dialog).getByLabelText("Date"), { target: { value: "2020-01-01" } });

    expect(await within(dialog).findByText(PASSED)).toBeInTheDocument();
    expect(within(dialog).getByText(PASSED_HINT)).toBeInTheDocument();
    expect(backend.callsTo("POST /api/cron")).toHaveLength(0);
  });

  it("asks for the date, where it is missing, instead of sending a task with none", async () => {
    const { user, backend } = await cronAt();
    await user.click(screen.getByRole("button", { name: "New task" }));
    const dialog = await screen.findByRole("dialog", { name: "New scheduled task" });
    await user.type(within(dialog).getByLabelText("Name"), "reindex");
    await user.type(within(dialog).getByLabelText("Command"), "true");
    await user.click(within(dialog).getByRole("radio", { name: "Once" }));
    fireEvent.change(within(dialog).getByLabelText("Date"), { target: { value: "" } });

    await user.click(within(dialog).getByRole("button", { name: "Create task" }));

    expect(await within(dialog).findByText("Choose the date.")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/cron")).toHaveLength(0);
  });
});

describe("a task that runs once, in the list", () => {
  it("is said by its moment on the server's clock with the zone, and a task that repeats still by its words", async () => {
    const { table } = await cronAt([REPEATING, ONCE_TASK]);

    const once = (await within(table).findByText("reindex")).closest("tr");
    if (!once) throw new Error("no row");
    expect(within(once).getByText(/Once, .*14:30 UTC\+01:00/)).toBeInTheDocument();
    const repeating = within(table).getByText("nightly-backup").closest("tr");
    if (!repeating) throw new Error("no row");
    expect(within(repeating).getByText("Every day at 02:00")).toBeInTheDocument();
  });

  it("opens on the choice it was made with, and its date and time, when it is edited", async () => {
    const { user, table } = await cronAt([ONCE_TASK]);
    const row = (await within(table).findByText("reindex")).closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Actions for reindex" }));
    await user.click(await screen.findByRole("menuitem", { name: "Edit" }));

    const dialog = await screen.findByRole("dialog", { name: "Edit reindex" });
    expect(within(dialog).getByRole("radio", { name: "Once" })).toBeChecked();
    expect(within(dialog).getByLabelText("Date")).toHaveValue("2026-12-01");
    expect(within(dialog).getByLabelText("At")).toHaveValue("14:30");
  });
});

describe("what ran once", () => {
  it("is listed with how each ended, the late one marked, and nothing for a server that has run none", async () => {
    const { table } = await cronAt([REPEATING], { "GET /api/cron/history": () => json(200, HISTORY) });

    const section = await screen.findByRole("region", { name: "Ran once" });
    expect(within(section).getByText(/kept for 30 days/)).toBeInTheDocument();
    const rows = within(within(section).getByRole("table", { name: "One-time tasks that ran" })).getAllByRole("row").slice(1);
    expect(rows[0]?.textContent).toContain("cleanup");
    expect(rows[0]?.textContent).toContain("Late");
    expect(rows[0]?.textContent).toContain("exit-code");
    expect(rows[1]?.textContent).toContain("reindex-shop");
    expect(rows[1]?.textContent).toContain("Succeeded");
    expect(rows[1]?.textContent).not.toContain("Late");
    // The table of tasks is the page's, and is still there.
    expect(within(table).getByText("nightly-backup")).toBeInTheDocument();
  });

  it("says nothing when no one-time task has run", async () => {
    await cronAt();

    expect(screen.queryByRole("region", { name: "Ran once" })).toBeNull();
  });

  it("shows what a task printed, because the units that held it are gone", async () => {
    const { user } = await cronAt([REPEATING], { "GET /api/cron/history": () => json(200, HISTORY) });
    const section = await screen.findByRole("region", { name: "Ran once" });

    await user.click(within(section).getByText("cleanup"));

    const drawer = await screen.findByRole("dialog", { name: "What cleanup printed" });
    expect(within(drawer).getByText("ERROR: disk full")).toBeInTheDocument();
  });

  it("schedules a task again: its form opens as a new one-time task made like it, for a date to choose", async () => {
    const { user, backend } = await cronAt([REPEATING], { "GET /api/cron/history": () => json(200, HISTORY) });
    const section = await screen.findByRole("region", { name: "Ran once" });
    await user.click(within(section).getByRole("button", { name: "Actions for reindex-shop" }));
    await user.click(await screen.findByRole("menuitem", { name: "Schedule again" }));

    const dialog = await screen.findByRole("dialog", { name: "New scheduled task" });
    expect(within(dialog).getByLabelText("Name")).toHaveValue("reindex-shop");
    expect(within(dialog).getByLabelText("Command")).toHaveValue("php artisan search:reindex");
    expect(within(dialog).getByRole("radio", { name: "Once" })).toBeChecked();
    expect(within(dialog).getByLabelText("Date")).toHaveValue("");
    fireEvent.change(within(dialog).getByLabelText("Date"), { target: { value: "2026-12-08" } });
    await user.click(within(dialog).getByRole("button", { name: "Create task" }));

    await waitFor(() => {
      expect(backend.callsTo("POST /api/cron")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/cron")[0]?.body).toMatchObject({
      name: "reindex-shop",
      command: "php artisan search:reindex",
      app_domain: "shop.example.com",
      once_at: "2026-12-08 02:00",
    });
  });

  it("says in a line, and not as a failure, that a server with an older Noust has none", async () => {
    fakeBackend({
      ...fleetRoutes(centralSession(), [WEB2]),
      ...onNode("web-2", {
        "GET /api/openapi.json": () => json(200, { paths: { "/api/cron": { get: {}, post: {} } } }),
        "GET /api/cron": () => json(200, { jobs: [REPEATING], total: 1 }),
      }),
    });
    renderConsole("/n/web-2/cron");

    expect(await screen.findByText("web-2 runs a Noust older than this console and has no one-time tasks.")).toBeInTheDocument();
    expect(screen.queryByRole("region", { name: "Ran once" })).toBeNull();
  });

  it("says the same when the server's schema could not be read and its own answer is a 404", async () => {
    fakeBackend({
      ...fleetRoutes(centralSession(), [WEB2]),
      ...onNode("web-2", {
        "GET /api/cron": () => json(200, { jobs: [REPEATING], total: 1 }),
        "GET /api/cron/history": () => problem(404, "not_found", "Not Found"),
      }),
    });
    renderConsole("/n/web-2/cron");

    expect(await screen.findByText("web-2 runs a Noust older than this console and has no one-time tasks.")).toBeInTheDocument();
  });

  it("has no accessibility violations, and speaks Spanish", async () => {
    const { container, user } = await cronAt([REPEATING, ONCE_TASK], { "GET /api/cron/history": () => json(200, HISTORY) });
    await screen.findByRole("region", { name: "Ran once" });
    await expectNoAxeViolations(container);

    await act(() => setLocale("es"));
    expect(await screen.findByRole("region", { name: "Ejecutadas una vez" })).toBeInTheDocument();
    expect(screen.getByText("Tarde")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Nueva tarea" }));
    const dialog = await screen.findByRole("dialog", { name: "Nueva tarea programada" });
    await user.click(within(dialog).getByRole("radio", { name: "Una vez" }));
    expect(await within(dialog).findByText("La hora es la del servidor. La tarea se retira sola cuando se ha ejecutado.")).toBeInTheDocument();
    await act(() => setLocale("en"));
  });
});
