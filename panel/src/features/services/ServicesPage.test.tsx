import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it } from "vitest";

import type { ServiceList } from "../../api/queries/services";
import { setLocale } from "../../app/locale";
import { expectNoAxeViolations } from "../../test/axe";
import { renderConsole } from "../../test/console";
import { fakeBackend, json, onNode, problem, signedInRoutes } from "../../test/fakes";
import type { RouteHandler } from "../../test/fakes";
import { onDesktop, serverRoutes } from "../server/testing";

const SERVICES: ServiceList["services"] = [
  {
    name: "wasm-shop-example-com",
    description: "/usr/bin/node server.js",
    active: true,
    enabled: true,
    status: "running",
    pid: 4213,
    uptime: "Wed 2026-09-24 10:00:00 UTC",
    memory: "104857600",
    managed: true,
    active_state: "active",
    sub_state: "running",
    result: "success",
  },
  {
    name: "wasm-admin-example-com",
    description: "/usr/bin/node server.js",
    active: false,
    enabled: false,
    status: "stopped",
    pid: null,
    uptime: null,
    memory: null,
    managed: true,
    active_state: "inactive",
    sub_state: "dead",
    result: "success",
  },
];

/** The machine's units, once "Show all units" asks for `noust_only=false`: Noust's own, plus one it did not create. */
const ALL_SERVICES: ServiceList["services"] = [
  ...SERVICES,
  {
    name: "postgresql",
    description: null,
    active: true,
    enabled: true,
    status: "running",
    pid: 908,
    uptime: "Wed 2026-09-24 09:00:00 UTC",
    memory: "31457280",
    managed: false,
    active_state: "active",
    sub_state: "running",
    result: "success",
  },
];

/** Answers `GET /api/services` scoped by `noust_only`, the way the real endpoint does. */
function scopedServicesRoute(): RouteHandler {
  return (call) =>
    call.search.get("noust_only") === "false"
      ? json(200, { services: ALL_SERVICES, total: ALL_SERVICES.length })
      : json(200, { services: SERVICES, total: SERVICES.length });
}

beforeEach(onDesktop);

async function servicesAt(path = "/server/services", extra: Record<string, RouteHandler> = {}) {
  const backend = fakeBackend({
    ...signedInRoutes(),
    ...serverRoutes(),
    "GET /api/services": () => json(200, { services: SERVICES, total: SERVICES.length }),
    ...extra,
  });
  const harness = renderConsole(path);
  await screen.findByRole("heading", { level: 1, name: "Server" });
  const table = await screen.findByRole("region", { name: /Services/ });
  return { ...harness, backend, table };
}

describe("the services tab", () => {
  it("lists every seeded service with its state and boot setting", async () => {
    const { table } = await servicesAt();
    const running = await within(table).findByText("wasm-shop-example-com");
    const row = running.closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("Running")).toBeInTheDocument();
    // Starting at boot is an on/off at a glance: green with a dot, grey with a ring.
    expect(within(row).getByText("Starts")).toHaveAttribute("data-state", "running");

    const stopped = within(table).getByText("wasm-admin-example-com").closest("tr");
    if (!stopped) throw new Error("no row");
    expect(within(stopped).getByText("Stopped")).toBeInTheDocument();
    expect(within(stopped).getByText("Does not start")).toHaveAttribute("data-state", "stopped");
  });

  it("is a tab of the Server area, with the old address sending there", async () => {
    fakeBackend({ ...signedInRoutes(), ...serverRoutes(), "GET /api/services": () => json(200, { services: SERVICES, total: SERVICES.length }) });
    const { location } = renderConsole("/services?q=shop");
    await screen.findByRole("heading", { level: 1, name: "Server" });
    await waitFor(() => {
      expect(location().pathname).toBe("/server/services");
    });
    expect(location().search).toEqual({ q: "shop" });
    expect(screen.getByRole("link", { name: "Services" })).toHaveAttribute("aria-current", "page");
  });

  it("keeps the server a node's old address was on", async () => {
    fakeBackend({
      ...signedInRoutes(),
      "GET /api/nodes": () => json(200, { nodes: [{ name: "web-2", status: "reachable", version: "3.1.0" }], total: 1 }),
      ...onNode("web-2", { ...serverRoutes(), "GET /api/services": () => json(200, { services: SERVICES, total: SERVICES.length }), "GET /api/apps": () => json(200, { apps: [], total: 0 }) }),
    });
    const { history } = renderConsole("/n/web-2/services");
    await waitFor(() => {
      expect(history.location.pathname).toBe("/n/web-2/server/services");
    });
    expect(await screen.findByText("wasm-shop-example-com")).toBeInTheDocument();
  });

  it("names the application a unit runs", async () => {
    const { table } = await servicesAt("/server/services", {
      "GET /api/apps": () => json(200, { total: 1, apps: [{ domain: "shop.example.com", name: "shop", status: "running", active: true, enabled: true, layout: "inplace", keep_releases: 5, webhook_enabled: false, zero_downtime: false, unit: "wasm-shop-example-com.service" }] }),
    });
    const row = (await within(table).findByText("wasm-shop-example-com")).closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("Runs shop.example.com")).toBeInTheDocument();
  });

  it("filters by the search box, written into the URL", async () => {
    const { table, location, user } = await servicesAt();
    await within(table).findByText("wasm-shop-example-com");
    await user.type(screen.getByRole("searchbox", { name: "Search services" }), "admin");
    await waitFor(() => {
      expect(location().search).toEqual({ q: "admin" });
    });
    expect(within(table).queryByText("wasm-shop-example-com")).not.toBeInTheDocument();
  });

  it("filters by state from the address, and clears a filter that matches nothing", async () => {
    const { table, user, location } = await servicesAt("/server/services?state=stopped");
    await within(table).findByText("wasm-admin-example-com");
    expect(within(table).queryByText("wasm-shop-example-com")).not.toBeInTheDocument();
    expect(screen.getByText("1 of 2 services")).toBeInTheDocument();

    await user.type(screen.getByRole("searchbox", { name: "Search services" }), "shop");
    expect(await screen.findByText("No service matches these filters.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Clear filters" }));
    await waitFor(() => {
      expect(location().search).toEqual({});
    });
  });

  it("creates a service in simple mode through POST /api/services", async () => {
    const { user, backend, table } = await servicesAt("/server/services", {
      "POST /api/services": () => json(200, { success: true, message: "Service created: e2e-worker", service: "e2e-worker" }),
    });
    await within(table).findByText("wasm-shop-example-com");
    await user.click(screen.getByRole("button", { name: "New service" }));
    const dialog = await screen.findByRole("dialog", { name: "New service" });
    await user.type(within(dialog).getByLabelText("Name", { exact: true }), "e2e-worker");
    await user.type(within(dialog).getByLabelText("Command", { exact: true }), "/usr/bin/node worker.js");
    await user.click(within(dialog).getByRole("button", { name: "Create service" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/services")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/services")[0]?.body).toMatchObject({ name: "e2e-worker", command: "/usr/bin/node worker.js" });
    expect(await screen.findByText("Created e2e-worker")).toBeInTheDocument();
  });

  it("confirms it's you before creating a service, and retries once confirmed", async () => {
    let elevated = false;
    const { user, backend, table } = await servicesAt("/server/services", {
      "POST /api/services": () => {
        if (!elevated) return problem(403, "elevation_required", "Confirm it's you to continue.");
        return json(200, { success: true, message: "Service created: e2e-worker", service: "e2e-worker" });
      },
      "POST /api/auth/elevate": () => {
        elevated = true;
        return json(200, { elevated_until: new Date(Date.now() + 600_000).toISOString() });
      },
    });
    await within(table).findByText("wasm-shop-example-com");
    await user.click(screen.getByRole("button", { name: "New service" }));
    const dialog = await screen.findByRole("dialog", { name: "New service" });
    await user.type(within(dialog).getByLabelText("Name", { exact: true }), "e2e-worker");
    await user.type(within(dialog).getByLabelText("Command", { exact: true }), "/usr/bin/node worker.js");
    await user.click(within(dialog).getByRole("button", { name: "Create service" }));

    const confirm = await screen.findByRole("dialog", { name: "Confirm it's you" });
    await user.type(within(confirm).getByLabelText("Authentication code"), "123456");
    await user.click(within(confirm).getByRole("button", { name: "Confirm" }));

    expect(await screen.findByText("Created e2e-worker")).toBeInTheDocument();
    expect(backend.callsTo("POST /api/services")).toHaveLength(2);
  });

  it("says what a service is on a machine with none of Noust's, and offers the system's", async () => {
    fakeBackend({ ...signedInRoutes(), ...serverRoutes(), "GET /api/services": () => json(200, { services: [], total: 0 }) });
    const { user, location } = renderConsole("/server/services");
    expect(await screen.findByRole("heading", { level: 2, name: "No services yet" })).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Show the system's services" }));
    await waitFor(() => {
      expect(location().search).toEqual({ all: true });
    });
  });

  it("scopes the request to Noust's own units by default", async () => {
    const { backend, table } = await servicesAt("/server/services", { "GET /api/services": scopedServicesRoute() });
    await within(table).findByText("wasm-shop-example-com");
    await waitFor(() => {
      expect(backend.callsTo("GET /api/services").some((call) => call.search.get("noust_only") === "true")).toBe(true);
    });
    expect(within(table).queryByText("postgresql")).not.toBeInTheDocument();
    // Every row is Noust's: a column saying so on each would say nothing.
    expect(within(table).queryByRole("columnheader", { name: /Managed/ })).not.toBeInTheDocument();
  });

  it("shows every unit, a foreign one read only, when the whole system is chosen", async () => {
    const { user, backend, table, location } = await servicesAt("/server/services", { "GET /api/services": scopedServicesRoute() });
    await within(table).findByText("wasm-shop-example-com");

    await user.click(screen.getByRole("radio", { name: "Whole system" }));

    await waitFor(() => {
      expect(backend.callsTo("GET /api/services").some((call) => call.search.get("noust_only") === "false")).toBe(true);
    });
    expect(location().search).toEqual({ all: true });

    const foreignRow = (await within(table).findByText("postgresql")).closest("tr");
    expect(within(table).getByRole("columnheader", { name: /Managed/ })).toBeInTheDocument();
    if (!foreignRow) throw new Error("no row");
    await user.click(within(foreignRow).getByRole("button", { name: "Actions for postgresql" }));
    expect(await screen.findByRole("menuitem", { name: "View logs" })).toBeInTheDocument();
    expect(screen.queryByRole("menuitem", { name: "Restart" })).not.toBeInTheDocument();
  });

  it("asks before stopping a unit from its row", async () => {
    const { user, backend, table } = await servicesAt("/server/services", {
      "POST /api/services/wasm-shop-example-com/stop": () => json(200, { success: true, message: "Stopped" }),
    });
    const row = (await within(table).findByText("wasm-shop-example-com")).closest("tr");
    if (!row) throw new Error("no row");
    await user.click(within(row).getByRole("button", { name: "Actions for wasm-shop-example-com" }));
    await user.click(await screen.findByRole("menuitem", { name: "Stop" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Stop wasm-shop-example-com?" });
    expect(backend.callsTo("POST /api/services/wasm-shop-example-com/stop")).toHaveLength(0);
    await user.click(within(dialog).getByRole("button", { name: "Stop service" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/services/wasm-shop-example-com/stop")).toHaveLength(1);
    });
  });

  it("has no accessibility violations", async () => {
    const { table } = await servicesAt();
    await within(table).findByText("wasm-shop-example-com");
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("has no accessibility violations with every unit shown", async () => {
    const { table } = await servicesAt("/server/services?all=1", { "GET /api/services": scopedServicesRoute() });
    await within(table).findByText("postgresql");
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});

describe("the services tab, in Spanish", () => {
  it("shows the state, boot setting and chrome translated, and passes axe", async () => {
    await act(async () => {
      await setLocale("es");
    });
    fakeBackend({
      ...signedInRoutes(),
      ...serverRoutes(),
      "GET /api/services": () => json(200, { services: SERVICES, total: SERVICES.length }),
    });
    renderConsole("/server/services");
    await screen.findByRole("heading", { level: 1, name: "Servidor" });
    const table = await screen.findByRole("region", { name: /Servicios/ });
    const running = await within(table).findByText("wasm-shop-example-com");
    const row = running.closest("tr");
    if (!row) throw new Error("no row");
    expect(within(row).getByText("En ejecución")).toBeInTheDocument();
    expect(within(row).getByText("Arranca")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Nuevo servicio" })).toBeInTheDocument();
    expect(screen.getByRole("searchbox", { name: "Buscar servicios" })).toBeInTheDocument();
    await expectNoAxeViolations(screen.getByRole("main"));
  });
});
