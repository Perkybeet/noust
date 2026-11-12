import { act, screen, waitFor, within } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { setLocale } from "../../../app/locale";
import { expectNoAxeViolations } from "../../../test/axe";
import { renderConsole } from "../../../test/console";
import { fakeBackend, json } from "../../../test/fakes";
import type { RouteHandler } from "../../../test/fakes";
import { centralSession, fleetRoutes } from "../../fleet/testFixtures";

/** A screen as wide as a desktop's: tables are tables, not the phone's card rows. */
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

const GB = 1024 ** 3;

function node(name: string, over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    node: name,
    follow: null,
    following: true,
    last_pass_at: "2026-11-10T02:05:00+00:00",
    last_ok_at: "2026-11-10T02:05:00+00:00",
    last_error: null,
    last_error_code: null,
    failures: 0,
    support: { mode: "full" },
    held: { count: 1, bytes: 2048 },
    newest: "2026-11-10T02:00:00+00:00",
    moving: null,
    ...over,
  };
}

function status(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    following: { following: 3, of: 3, exceptions: [] },
    nodes: [
      node("web-2"),
      node("db-1", {
        last_error: "403: the access level of db-1 does not allow reading copies",
        last_error_code: "ceiling",
        failures: 3,
      }),
      node("old-1", { support: { mode: "databases_only" }, held: { count: 0, bytes: 0 } }),
    ],
    settings: {
      enabled: true,
      kinds: ["app", "database", "volume", "noust"],
      rate_limit_kbps: null,
      poll_seconds: 60,
      pass_hours: 6,
      retention_count: 30,
      retention_days: null,
      retention_gb: null,
      min_free_gb: 1,
    },
    space: { total: 500 * GB, free: 120 * GB, used: 30 * GB, min_free: GB, available: 119 * GB, low: false },
    directory: "/data/backups/.collected",
    ...over,
  };
}

function copy(over: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    node: "web-2",
    kind: "app",
    id: "shop_1",
    owner: "shop.example.com",
    origin: "scheduled",
    created_at: "2026-11-10T02:00:00+00:00",
    collected_at: "2026-11-10T02:05:00+00:00",
    size: 2048,
    files: [{ name: "shop_1.tar.gz", size: 2048, sha256: "a".repeat(64), role: "archive" }],
    verified: "digest",
    state: "held",
    meta: { domain: "shop.example.com" },
    ...over,
  };
}

function routes(extra: Record<string, RouteHandler> = {}, held = [copy()]): Record<string, RouteHandler> {
  return {
    ...fleetRoutes(centralSession()),
    "GET /api/central-backups/status": () => json(200, status()),
    "GET /api/central-backups": () => json(200, { items: held, total: held.length }),
    ...extra,
  };
}

async function openPage(extra: Record<string, RouteHandler> = {}, held = [copy()]) {
  const backend = fakeBackend(routes(extra, held));
  const harness = renderConsole("/fleet/central-backups");
  await screen.findByRole("heading", { level: 1, name: "Copies on the central" });
  return { ...harness, backend };
}

describe("the copies on the central", () => {
  it("lists what the central holds and how each server is doing, with no accessibility violations", async () => {
    await openPage();
    const copies = await screen.findByRole("region", { name: "Copies held by the central" });
    const row = await within(copies).findByText("shop.example.com");
    expect(row).toBeInTheDocument();
    expect(within(copies).getByText("Checksum matched")).toBeInTheDocument();
    const servers = await screen.findByRole("region", { name: "How collecting is going with each server" });
    expect(within(servers).getByText("Up to date")).toBeInTheDocument();
    expect(within(servers).getByText("Only database dumps")).toBeInTheDocument();
    // A refusal says it in its own words and then the server's, verbatim, with the fix above them.
    expect(within(servers).getByText("Its access level does not allow it")).toBeInTheDocument();
    expect(within(servers).getByText(/noust fleet access/)).toBeInTheDocument();
    expect(within(servers).getByText("403: the access level of db-1 does not allow reading copies")).toBeInTheDocument();
    expect(screen.getAllByRole("heading", { level: 1 })).toHaveLength(1);
    await expectNoAxeViolations(screen.getByRole("main"));
  });

  it("says that it is one copy on one machine, and where to put another", async () => {
    await openPage();

    expect(await screen.findByText("This is one copy, and it is on this machine")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "Add a destination" })).toHaveAttribute("href", "/backups/destinations");
  });

  it("says nothing is taken when no server is followed, and offers to turn it on where the copies would be", async () => {
    const { user } = await openPage({
      "GET /api/central-backups/status": () =>
        json(200, status({ following: { following: 0, of: 3, exceptions: [] }, nodes: [node("web-2", { following: false })], settings: { ...(status()["settings"] as object), enabled: false } })),
    }, []);

    expect(await screen.findByText("Not collecting from any server")).toBeInTheDocument();
    expect(screen.queryByText("This is one copy, and it is on this machine")).not.toBeInTheDocument();
    expect(await screen.findByText("Turn collecting on and the copies of your servers will be taken here.")).toBeInTheDocument();
    await user.click(screen.getByRole("button", { name: "Turn on collecting" }));
    expect(await screen.findByRole("dialog", { name: "Collecting settings" })).toBeInTheDocument();
  });

  it("warns when the room is running out, and says nothing is deleted to make it", async () => {
    await openPage({
      "GET /api/central-backups/status": () => json(200, status({ space: { total: 100 * GB, free: 2 * GB, used: 60 * GB, min_free: GB, available: GB, low: true } })),
    });

    expect(await screen.findByText("The central is running out of room for copies")).toBeInTheDocument();
    expect(screen.getByText(/Nothing is deleted to make room/)).toBeInTheDocument();
  });

  it("names the copy being taken while a server is moving one", async () => {
    await openPage({
      "GET /api/central-backups/status": () =>
        json(200, status({ nodes: [node("web-2", { moving: { item: "app: shop.example.com", file: "shop_1.tar.gz", done: 1024 ** 2, size: 4 * 1024 ** 2, taken: 0, todo: 1 } })] })),
    });

    expect(await screen.findByText("Taking app: shop.example.com")).toBeInTheDocument();
    expect(screen.getByText("1.0 MB of 4.0 MB")).toBeInTheDocument();
  });

  it("asks for a pass now, for every server and for one", async () => {
    const { user, backend } = await openPage({ "POST /api/central-backups/collect": () => json(202, status()) });

    await user.click(await screen.findByRole("button", { name: "Take copies now" }));
    await waitFor(() => expect(backend.callsTo("POST /api/central-backups/collect")).toHaveLength(1));
    expect(backend.callsTo("POST /api/central-backups/collect")[0]?.body).toEqual({ node: null, force: true });

    const servers = await screen.findByRole("region", { name: "How collecting is going with each server" });
    await user.click(within(servers).getByRole("button", { name: "Actions for web-2" }));
    await user.click(await screen.findByRole("menuitem", { name: "Take its copies now" }));
    await waitFor(() => expect(backend.callsTo("POST /api/central-backups/collect")).toHaveLength(2));
    expect(backend.callsTo("POST /api/central-backups/collect")[1]?.body).toEqual({ node: "web-2", force: true });
  });

  it("gives a server its own answer, and gives it back to the fleet", async () => {
    const { user, backend } = await openPage({ "PUT /api/central-backups/nodes/web-2": () => json(200, status()) });
    const servers = await screen.findByRole("region", { name: "How collecting is going with each server" });

    await user.click(within(servers).getByRole("button", { name: "Actions for web-2" }));
    await user.click(await screen.findByRole("menuitem", { name: "Never collect" }));

    await waitFor(() => expect(backend.callsTo("PUT /api/central-backups/nodes/web-2")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/central-backups/nodes/web-2")[0]?.body).toEqual({ follow: false });
  });

  describe("the settings", () => {
    it("are saved whole, with an emptied limit sent as 0 so that it is taken off", async () => {
      const { user, backend } = await openPage({ "PUT /api/central-backups/settings": () => json(200, status()) });

      await user.click(await screen.findByRole("button", { name: "Collecting settings" }));
      const drawer = await screen.findByRole("dialog", { name: "Collecting settings" });
      await user.type(within(drawer).getByLabelText(/Speed limit/), "2048");
      await user.clear(within(drawer).getByLabelText(/Copies of each application/));
      await user.click(within(drawer).getByRole("button", { name: "Save settings" }));

      await waitFor(() => expect(backend.callsTo("PUT /api/central-backups/settings")).toHaveLength(1));
      expect(backend.callsTo("PUT /api/central-backups/settings")[0]?.body).toEqual({
        enabled: true,
        kinds: ["app", "database", "volume", "noust"],
        rate_limit_kbps: 2048,
        retention_count: 0,
        retention_days: 0,
        retention_gb: 0,
        min_free_gb: 1,
      });
      await waitFor(() => expect(screen.queryByRole("dialog", { name: "Collecting settings" })).not.toBeInTheDocument());
    });

    it("name the field that is wrong and send nothing", async () => {
      const { user, backend } = await openPage();

      await user.click(await screen.findByRole("button", { name: "Collecting settings" }));
      const drawer = await screen.findByRole("dialog", { name: "Collecting settings" });
      await user.type(within(drawer).getByLabelText(/Speed limit/), "8");
      await user.click(within(drawer).getByRole("button", { name: "Save settings" }));

      expect(await within(drawer).findByText("Type a speed from 16 to 10,000,000 KB/s, or leave it empty.")).toBeInTheDocument();
      expect(backend.callsTo("PUT /api/central-backups/settings")).toHaveLength(0);
    });
  });

  describe("restoring a copy", () => {
    it("asks for the name of what it replaces, and gives it back to the server it came from as a job", async () => {
      const { user, backend } = await openPage({
        "POST /api/central-backups/web-2/app/restore": () => json(202, { job_id: "j-1", message: "Queued", job: { id: "j-1", status: "queued" } }),
      });
      const copies = await screen.findByRole("region", { name: "Copies held by the central" });

      await user.click(within(copies).getByRole("button", { name: "Actions for shop.example.com" }));
      await user.click(await screen.findByRole("menuitem", { name: "Restore on a server" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Restore shop.example.com from the central" });
      const action = within(dialog).getByRole("button", { name: "Restore" });
      expect(action).toBeDisabled();
      await user.type(within(dialog).getByLabelText(/Type shop.example.com to confirm/), "shop.example.com");
      await user.click(action);

      await waitFor(() => expect(backend.callsTo("POST /api/central-backups/web-2/app/restore")).toHaveLength(1));
      expect(backend.callsTo("POST /api/central-backups/web-2/app/restore")[0]?.body).toEqual({
        item: "shop_1",
        target_node: "web-2",
        target: { domain: "shop.example.com" },
        options: { restore_env: false },
      });
    });

    it("can go to another server, and says that it passes through the central", async () => {
      const { user, backend } = await openPage({
        "POST /api/central-backups/web-2/app/restore": () => json(202, { job_id: "j-2", message: "Queued", job: { id: "j-2", status: "queued" } }),
      });
      const copies = await screen.findByRole("region", { name: "Copies held by the central" });

      await user.click(within(copies).getByRole("button", { name: "Actions for shop.example.com" }));
      await user.click(await screen.findByRole("menuitem", { name: "Restore on a server" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Restore shop.example.com from the central" });
      await user.click(within(dialog).getByRole("combobox", { name: "Restore on" }));
      await user.click(await screen.findByRole("option", { name: "db-1" }));
      expect(await within(dialog).findByText(/passes the whole copy through the central's connection/)).toBeInTheDocument();
      await user.type(within(dialog).getByLabelText(/Type shop.example.com to confirm/), "shop.example.com");
      await user.click(within(dialog).getByRole("button", { name: "Restore" }));

      await waitFor(() => expect(backend.callsTo("POST /api/central-backups/web-2/app/restore")).toHaveLength(1));
      expect(backend.callsTo("POST /api/central-backups/web-2/app/restore")[0]?.body).toMatchObject({ target_node: "db-1" });
    });

    it("asks only once for a copy restored beside what is there, because nothing is lost", async () => {
      const { user, backend } = await openPage({
        "POST /api/central-backups/web-2/app/restore": () => json(202, { job_id: "j-3", message: "Queued", job: { id: "j-3", status: "queued" } }),
      });
      const copies = await screen.findByRole("region", { name: "Copies held by the central" });

      await user.click(within(copies).getByRole("button", { name: "Actions for shop.example.com" }));
      await user.click(await screen.findByRole("menuitem", { name: "Restore on a server" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Restore shop.example.com from the central" });
      const domain = within(dialog).getByLabelText("Application domain");
      await user.clear(domain);
      await user.type(domain, "shop-copy.example.com");
      expect(within(dialog).queryByLabelText(/Type .* to confirm/)).not.toBeInTheDocument();
      await user.click(within(dialog).getByRole("button", { name: "Restore" }));

      await waitFor(() => expect(backend.callsTo("POST /api/central-backups/web-2/app/restore")).toHaveLength(1));
      expect(backend.callsTo("POST /api/central-backups/web-2/app/restore")[0]?.body).toMatchObject({ target: { domain: "shop-copy.example.com" } });
    });

    it("puts a copy of Noust's own state on the server and says that it is not restored from here", async () => {
      const { user } = await openPage({}, [copy({ kind: "noust", id: "noust_1", owner: "noust", meta: {} })]);
      const copies = await screen.findByRole("region", { name: "Copies held by the central" });

      await user.click(within(copies).getByRole("button", { name: "Actions for noust" }));
      await user.click(await screen.findByRole("menuitem", { name: "Restore on a server" }));
      const dialog = await screen.findByRole("alertdialog", { name: "Restore noust from the central" });

      expect(within(dialog).getByText(/is not restored from here|put on the server but not restored from here/)).toBeInTheDocument();
      expect(within(dialog).queryByLabelText(/Type .* to confirm/)).not.toBeInTheDocument();
    });
  });

  it("lets a copy go from the central after one question, and says the server's own stays", async () => {
    const { user, backend } = await openPage({ "DELETE /api/central-backups/web-2/app": () => json(200, { items: [], total: 0 }) });
    const copies = await screen.findByRole("region", { name: "Copies held by the central" });

    await user.click(within(copies).getByRole("button", { name: "Actions for shop.example.com" }));
    await user.click(await screen.findByRole("menuitem", { name: "Delete from the central" }));
    const dialog = await screen.findByRole("alertdialog", { name: "Delete this copy from the central?" });
    expect(within(dialog).getByText(/Its own copy, if it still has one, stays where it is/)).toBeInTheDocument();
    await user.click(within(dialog).getByRole("button", { name: "Delete the copy" }));

    await waitFor(() => expect(backend.callsTo("DELETE /api/central-backups/web-2/app")).toHaveLength(1));
    expect(backend.callsTo("DELETE /api/central-backups/web-2/app")[0]?.search.get("item")).toBe("shop_1");
  });

  it("shows no action to someone who may only read", async () => {
    const readOnly = centralSession({}, { permissions: ["self", "backups.read", "fleet.read"] });
    fakeBackend({
      ...fleetRoutes(readOnly),
      "GET /api/central-backups/status": () => json(200, status()),
      "GET /api/central-backups": () => json(200, { items: [copy()], total: 1 }),
    });
    renderConsole("/fleet/central-backups");
    await screen.findByRole("heading", { level: 1, name: "Copies on the central" });
    const copies = await screen.findByRole("region", { name: "Copies held by the central" });
    await within(copies).findByText("shop.example.com");

    // The page's own action stays where it is, disabled, and the page says once that it can only be read.
    expect(screen.getByRole("button", { name: "Take copies now" })).toBeDisabled();
    expect(screen.queryByRole("button", { name: "Collecting settings" })).not.toBeInTheDocument();
    expect(within(copies).queryByRole("button", { name: "Actions for shop.example.com" })).not.toBeInTheDocument();
  });

  it("reads in Spanish", async () => {
    await openPage();
    await act(() => setLocale("es"));

    expect(await screen.findByRole("heading", { level: 1, name: "Copias en la central" })).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Recoger copias ahora" })).toBeInTheDocument();
  });
});

describe("the central as a place for copies, among the destinations", () => {
  function destinationRoutes(extra: Record<string, RouteHandler> = {}): Record<string, RouteHandler> {
    return {
      ...routes(extra),
      "GET /api/backups": () => json(200, { backups: [], total: 0 }),
      "GET /api/backups/storage": () => json(200, { path: "/var/backups/noust", total_size: 0, total_size_human: "0 B", backup_count: 0, domains: [] }),
      "GET /api/backup-schedules": () => json(200, { schedules: [], total: 0 }),
      "GET /api/backup-destinations": () => json(200, { destinations: [], total: 0 }),
      "GET /api/backup-destinations/backends": () => json(200, { backends: [] }),
    };
  }

  it("is offered on the central's own page, with how many servers it comes for and where the copies are", async () => {
    fakeBackend(destinationRoutes());
    renderConsole("/backups/destinations");
    await screen.findByRole("heading", { level: 1, name: "Backups" });

    const card = await screen.findByRole("heading", { name: "The central" });
    expect(card).toBeInTheDocument();
    expect(await screen.findByText("Collecting from every server")).toBeInTheDocument();
    expect(screen.getByRole("link", { name: "See the copies" })).toHaveAttribute("href", "/fleet/central-backups");
    expect(screen.getByText("1 copy held, 2.0 KB")).toBeInTheDocument();
  });

  it("turns the fleet's collecting on and off from one switch", async () => {
    const backend = fakeBackend(
      destinationRoutes({
        "GET /api/central-backups/status": () =>
          json(200, status({ following: { following: 0, of: 3, exceptions: [] }, settings: { ...(status()["settings"] as object), enabled: false } })),
        "PUT /api/central-backups/settings": () => json(200, status()),
      }),
    );
    const { user } = renderConsole("/backups/destinations");
    await screen.findByRole("heading", { level: 1, name: "Backups" });

    await user.click(await screen.findByRole("switch", { name: "Collect from the fleet" }));

    await waitFor(() => expect(backend.callsTo("PUT /api/central-backups/settings")).toHaveLength(1));
    expect(backend.callsTo("PUT /api/central-backups/settings")[0]?.body).toEqual({ enabled: true });
  });

  it("is not offered on a server that is not a central", async () => {
    fakeBackend({
      ...destinationRoutes(),
      "GET /api/nodes": () => json(200, { items: [] }),
      "GET /api/auth/session": () => json(200, { ...centralSession(), central: { role: "server", sealed: false, locked: false } }),
    });
    renderConsole("/backups/destinations");
    await screen.findByRole("heading", { level: 1, name: "Backups" });

    expect(screen.queryByRole("heading", { name: "The central" })).not.toBeInTheDocument();
  });
});
