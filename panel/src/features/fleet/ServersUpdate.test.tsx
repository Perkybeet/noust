import { screen, waitFor, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { renderConsole } from "../../test/console";
import { fakeBackend, json } from "../../test/fakes";
import { PLAN, SUMMARY_VIEW, fleetRoutes } from "./testFixtures";

/**
 * Where a fleet is updated: in the list of its servers, one press per server and one in the header for all of them, with
 * the version each server's own package source offers on the button (it is read from the server, never written by hand).
 * The tests run at a phone's width, where every table is drawn as card rows.
 */

/**
 * The same servers, with what each one's source offers as the newest Noust. With a `state` every server is put in it, and
 * with "up_to_date" none has a package or a reboot waiting either, so nothing waits anywhere.
 */
type Fields = Record<string, unknown>;

function summaryOffering(version: string, state?: string) {
  return {
    ...SUMMARY_VIEW,
    items: SUMMARY_VIEW.items.map((item: Fields) => ({
      ...item,
      noust: { ...(item["noust"] as Fields), latest_version: version, ...(state !== undefined ? { update_state: state } : {}) },
      ...(state === "up_to_date" && item["server"] !== null ? { server: { ...(item["server"] as Fields), updates: { pending: 0, security: 0 }, reboot: { required: false } } } : {}),
    })),
  };
}

async function rowOf(list: HTMLElement, text: string): Promise<HTMLElement> {
  return waitFor(() => {
    const row = within(list)
      .queryAllByRole("listitem")
      .find((candidate) => within(candidate).queryAllByText(text).length > 0);
    if (row === undefined) throw new Error(`no row for ${text}`);
    return row;
  });
}

const NOUST_PLAN = { ...PLAN, action: "noust_update", title: "Update Noust", options: { target_version: null } };

describe("updating from the list of servers", () => {
  it("puts the version the server offers on the button that updates it, and none on a server that is current", { timeout: 30_000 }, async () => {
    fakeBackend({ ...fleetRoutes(), "GET /api/fleet/summary": () => json(200, summaryOffering("2.2.0")) });
    renderConsole("/fleet/servers");
    const table = await screen.findByRole("list", { name: "Servers of this fleet" });

    expect(within(await rowOf(table, "db-1")).getByRole("button", { name: "Update to 2.2.0" })).toBeInTheDocument();
    expect(within(await rowOf(table, "old-1")).getByRole("button", { name: "Update to 2.2.0" })).toBeInTheDocument();
    // A server on the newest says so in words, and has nothing to press.
    const current = await rowOf(table, "web-2");
    expect(within(current).getByText("up to date")).toBeInTheDocument();
    expect(within(current).queryByRole("button", { name: /^Update to/ })).not.toBeInTheDocument();
  });

  it("updates one server from its own row, through the plan every bulk action shows first", { timeout: 40_000 }, async () => {
    const backend = fakeBackend({
      ...fleetRoutes(),
      "GET /api/fleet/summary": () => json(200, summaryOffering("2.2.0")),
      "POST /api/fleet/actions": () => json(200, { plan: NOUST_PLAN, job: null }),
    });
    const { user } = renderConsole("/fleet/servers");
    const table = await screen.findByRole("list", { name: "Servers of this fleet" });
    await user.click(within(await rowOf(table, "db-1")).getByRole("button", { name: "Update to 2.2.0" }));
    const dialog = await screen.findByRole("dialog", { name: "Run an action on several servers" });
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/fleet/actions")).toHaveLength(1);
    });
    // Only that server, and the version is left to the server's own source: nothing typed here reaches the plan.
    expect(backend.callsTo("POST /api/fleet/actions")[0]?.body).toMatchObject({ action: "noust_update", targets: { nodes: ["db-1"] }, options: { target_version: null }, plan: true });
  });

  it("updates every server that waits from the header, and never the central, which updates from its own page", { timeout: 40_000 }, async () => {
    const backend = fakeBackend({
      ...fleetRoutes(),
      // Every server, the central included, is behind: the header still leaves the central out.
      "GET /api/fleet/summary": () => json(200, summaryOffering("2.2.0", "update_available")),
      "POST /api/fleet/actions": () => json(200, { plan: NOUST_PLAN, job: null }),
    });
    const { user } = renderConsole("/fleet/servers");
    await screen.findByRole("list", { name: "Servers of this fleet" });
    await user.click(await screen.findByRole("button", { name: "Update Noust on all" }));
    const dialog = await screen.findByRole("dialog", { name: "Run an action on several servers" });
    await user.click(within(dialog).getByRole("button", { name: "Show the plan" }));
    await waitFor(() => {
      expect(backend.callsTo("POST /api/fleet/actions")).toHaveLength(1);
    });
    expect(backend.callsTo("POST /api/fleet/actions")[0]?.body).toMatchObject({ action: "noust_update", targets: { nodes: ["web-2", "db-1", "old-1"] } });
  });

  it("draws no button in the header when every server is current", { timeout: 30_000 }, async () => {
    fakeBackend({ ...fleetRoutes(), "GET /api/fleet/summary": () => json(200, summaryOffering("2.0.0", "up_to_date")) });
    renderConsole("/fleet/servers");
    const table = await screen.findByRole("list", { name: "Servers of this fleet" });
    await rowOf(table, "web-2");
    expect(screen.queryByRole("button", { name: "Update Noust on all" })).not.toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /^Update to/ })).not.toBeInTheDocument();
  });

  it("lists in 'With an update' only the servers that wait for something", { timeout: 40_000 }, async () => {
    fakeBackend({ ...fleetRoutes(), "GET /api/fleet/summary": () => json(200, summaryOffering("2.2.0")) });
    renderConsole("/fleet/updates");
    const waiting = await screen.findByRole("list", { name: "Noust and system updates of every server" });
    await rowOf(waiting, "db-1");
    expect(within(waiting).queryAllByText("web-01")).toHaveLength(0);
  });

  it("says so in 'With an update' when no server waits for anything", { timeout: 40_000 }, async () => {
    fakeBackend({ ...fleetRoutes(), "GET /api/fleet/summary": () => json(200, summaryOffering("2.0.0", "up_to_date")) });
    renderConsole("/fleet/updates");
    expect(await screen.findByText("Every server is up to date.")).toBeInTheDocument();
  });
});
