import { describe, expect, it } from "vitest";

import { areaColumn } from "./columns";
import type { ColumnKind } from "./columns";
import {
  DESTS,
  allServersTargetOf,
  areaLanding,
  areaOf,
  areaOffered,
  areaTarget,
  dest,
  entryAt,
  fleetTarget,
  isFleetFace,
  paletteEntries,
  rootOf,
  serverTargetOf,
  shortcuts,
  sidebarFor,
  tabsById,
} from "./nav";
import type { Dest, NavContext } from "./nav";
import { fleetPathOf, isCentralOnlyPath, serverPathOfFleet } from "./nodeRoute";
import { VIEWS } from "../features/nav/views";
import type { FeatureKey } from "../features/central/features";

/**
 * The scope is in the address and the sections are the same with both (item 196), and what a server cannot do is offered by no list (item
 * 187). These are the properties that hold for every destination of the register, so a new one cannot break them without a test saying
 * which.
 */

const AREAS = DESTS.filter((entry) => entry.kind === "area" && entry.group !== "pinned");

/** Every function some destination of the register asks for. */
const KEYS: readonly FeatureKey[] = [...new Set([...DESTS.map((entry) => entry.feature), ...Object.values(VIEWS).flatMap((views) => views.map((view) => view.feature))].filter((key): key is FeatureKey => key !== undefined))].sort();

/** What a server offers when only one function is missing. */
const without = (missing: FeatureKey): NavContext => ({ features: (key) => key !== missing });

const hasKey = (entry: Dest, key: FeatureKey): boolean => entry.feature === key;

describe("the same address in both scopes", () => {
  it("is /fleet followed by the server's address, and back", () => {
    expect(fleetPathOf("/")).toBe("/fleet");
    expect(fleetPathOf("/databases")).toBe("/fleet/databases");
    expect(fleetPathOf("/databases/engines/")).toBe("/fleet/databases/engines");
    expect(serverPathOfFleet("/fleet")).toBe("/");
    expect(serverPathOfFleet("/fleet/databases/engines")).toBe("/databases/engines");
    expect(serverPathOfFleet("/databases")).toBeNull();
    expect(serverPathOfFleet("/fleetish")).toBeNull();
    for (const path of ["/", "/databases", "/backups/volumes", "/network/tunnels", "/server/services"]) {
      expect(serverPathOfFleet(fleetPathOf(path))).toBe(path);
    }
  });

  it("gives every section of the bar an address under /fleet, which is under the same area", () => {
    for (const entry of DESTS) {
      if (entry.scope === "fleet" || entry.to.includes("$") || isCentralOnlyPath(entry.to)) continue;
      if (entry.kind === "hidden" && entry.column === undefined) continue;
      const target = fleetTarget(entry);
      expect(target.startsWith("/fleet"), `${entry.id} opens ${target} from All servers`).toBe(true);
      expect(areaOf(target)?.id, `${entry.id}: ${target} is under another area`).toBe(rootOf(entry).id);
    }
  });

  it("goes from a section to its fleet page and back to the same section, for every section", () => {
    for (const entry of DESTS) {
      if (entry.scope === "fleet" || entry.to.includes("$") || isCentralOnlyPath(entry.to)) continue;
      if (entry.kind === "hidden" && entry.column === undefined) continue;
      expect(serverTargetOf(allServersTargetOf(entry.to)), entry.id).toBe(entry.to);
    }
  });

  it("opens a resource's page (an application, a database) at its area, never at nothing", () => {
    expect(allServersTargetOf("/apps/shop.example.com/logs")).toBe("/fleet/apps");
    expect(allServersTargetOf("/databases/postgresql/clientes")).toBe("/fleet/databases");
    expect(allServersTargetOf("/server/services/noust-web")).toBe("/fleet/servers");
    expect(allServersTargetOf("/nowhere")).toBe("/fleet");
  });

  it("knows which pages of the fleet are a page of their own and which the table of servers stands in for", () => {
    for (const face of ["/fleet", "/fleet/apps", "/fleet/servers", "/fleet/certificates", "/fleet/updates", "/fleet/software", "/fleet/placements", "/fleet/central-backups", "/fleet/jobs/42"]) {
      expect(isFleetFace(face), face).toBe(true);
    }
    for (const section of ["/fleet/databases", "/fleet/cron", "/fleet/catalog", "/fleet/logs", "/fleet/backups/schedules", "/fleet/network/tunnels"]) {
      expect(isFleetFace(section), section).toBe(false);
      expect(areaOf(section), section).toBeDefined();
    }
  });
});

describe("the second column", () => {
  const kinds = AREAS.map((entry) => entry.id as ColumnKind).filter((kind) => kind !== "overview");

  it("lists every section of the area with All servers that it lists with a server, each at its address under /fleet", () => {
    for (const kind of kinds) {
      const server = areaColumn(kind, {}, "server").flatMap((group) => group.items);
      const fleet = areaColumn(kind, {}, "fleet").flatMap((group) => group.items);
      const fleetIds = new Set(fleet.map((item) => item.id));
      for (const item of server) expect(fleetIds.has(item.id), `${kind}: ${item.id} is in a server's column and missing from All servers'`).toBe(true);
      // The central's own pages (Backups > Destinations) belong to no server: the selector stays where it was, so they are not a way out.
      for (const item of fleet) expect(item.to.startsWith("/fleet") || isCentralOnlyPath(item.to), `${kind}: ${item.id} leads to ${item.to}, which leaves All servers`).toBe(true);
    }
  });

  it("keeps the views of an area (Applications, Logs, Activity) with All servers, on its page under /fleet", () => {
    for (const kind of ["apps", "logs", "activity"] as const) {
      const fleet = areaColumn(kind, {}, "fleet").flatMap((group) => group.items);
      for (const view of VIEWS[kind] ?? []) expect(fleet.some((item) => item.id === `${kind}.view.${view.id}`), `${kind} view ${view.id}`).toBe(true);
    }
  });

  it("shows Backups, with All servers, the five sections it has with a server (the owner's third case)", () => {
    const labels = (scope: "server" | "fleet"): string[] => areaColumn("backups", {}, scope).flatMap((group) => group.items.map((item) => item.id));
    for (const id of ["backups", "backups.schedules", "backups.volumes", "backups.noust", "backups.destinations"]) {
      expect(labels("server")).toContain(id);
      expect(labels("fleet")).toContain(id);
    }
  });

  it("does not list a section twice when the fleet's page of it is the section (Updates and Software of the servers)", () => {
    const items = areaColumn("server", {}, "fleet").flatMap((group) => group.items);
    expect(items.filter((item) => item.to === "/fleet/updates")).toHaveLength(1);
    expect(items.filter((item) => item.to === "/fleet/software")).toHaveLength(1);
    expect(new Set(items.map((item) => item.to)).size).toBe(items.length);
  });
});

describe("a bar that follows the scope, whichever the page", () => {
  it("has the same areas with All servers as with a server, every one opening under /fleet (the owner's first case)", () => {
    const bar = sidebarFor().flat();
    expect(bar.map((entry) => entry.id)).toEqual(expect.arrayContaining(["databases", "cron", "catalog", "logs"]));
    for (const entry of bar) {
      const target = areaTarget(entry, "fleet");
      expect(target.startsWith("/fleet"), `${entry.id} opens ${target} from All servers`).toBe(true);
      // The page it opens lights the same area: a click that opens a page never leaves the bar on another.
      expect(areaOf(target)?.id).toBe(entry.id === "overview" ? "overview" : entry.id);
    }
  });

  it("opens the shortcuts of every area under /fleet from All servers", () => {
    for (const shortcut of shortcuts("fleet")) {
      if (shortcut.id === "settings") continue;
      expect(shortcut.to.startsWith("/fleet"), `${shortcut.id} opens ${shortcut.to}`).toBe(true);
    }
  });

  it("opens the palette's sections under /fleet from All servers, and a server's pages from a server", () => {
    const t = (key: string): string => key;
    const inFleet = paletteEntries({ t, scope: "fleet" });
    for (const entry of inFleet) {
      if (entry.scope === "fleet" || isCentralOnlyPath(entry.to) || entry.to === "/apps/new" || entry.to.startsWith("/fleet")) continue;
      throw new Error(`${entry.id} opens ${entry.to} from All servers, which is a server's page`);
    }
    const onServer = paletteEntries({ t, scope: "server" });
    expect(onServer.find((entry) => entry.id === "databases")?.to).toBe("/databases");
  });
});

describe("what a server cannot do is offered by no list (item 187)", () => {
  it("knows which functions the register asks for, and there are some", () => {
    expect(KEYS.length).toBeGreaterThan(10);
    expect(KEYS).toEqual(expect.arrayContaining(["applications", "databases", "scheduled_jobs", "services", "software", "system_clock", "root_shell"]));
  });

  for (const key of KEYS) {
    it(`leaves out of the bar, the columns, the tabs, the palette and the shortcuts what needs ${key}`, () => {
      const ctx = without(key);
      const t = (word: string): string => word;
      for (const group of sidebarFor(ctx)) for (const area of group) expect(areaOffered(area, ctx), `the bar offers ${area.id}`).toBe(true);
      for (const area of AREAS) {
        for (const scope of ["server"] as const) {
          for (const group of areaColumn(area.id as ColumnKind, ctx, scope)) {
            for (const item of group.items) {
              const entry = DESTS.find((candidate) => candidate.id === item.id);
              if (entry !== undefined) expect(hasKey(entry, key), `the column of ${area.id} offers ${item.id}`).toBe(false);
            }
          }
        }
        for (const tabItem of tabsById(area.id, ctx)) {
          const entry = DESTS.find((candidate) => candidate.to === tabItem.to && candidate.parent === area.id) ?? DESTS.find((candidate) => candidate.to === tabItem.to);
          if (entry?.kind === "tab") expect(hasKey(entry, key), `the tabs of ${area.id} offer ${entry.id}`).toBe(false);
        }
      }
      for (const entry of paletteEntries({ t, ...ctx })) {
        const registered = DESTS.find((candidate) => candidate.id === entry.id);
        if (registered !== undefined) expect(hasKey(registered, key), `the palette offers ${entry.id}`).toBe(false);
      }
      for (const shortcut of shortcuts("server", ctx)) {
        const registered = DESTS.find((candidate) => candidate.id === shortcut.id);
        if (registered !== undefined && registered.kind !== "area") expect(hasKey(registered, key), `a shortcut opens ${shortcut.id}`).toBe(false);
      }
    });
  }

  it("opens an area at the first section the server can do, and drops the area when it can do none", () => {
    const hub: NavContext = { features: (key) => !["applications", "published_ports", "tunnels", "firewall", "databases", "scheduled_jobs", "domains", "sites", "certificates", "dns_accounts", "noust_dns", "application_backups", "volume_backups", "services", "sites", "console_exposure", "software", "root_shell", "system_clock", "process_monitor", "status_page", "recipes", "system_logs", "server_security"].includes(key ?? "") };
    // Network's first section is the published ports, which a hub does not have: it opens at what it has.
    expect(areaLanding(dest("network"), hub).to).toBe("/network/listening");
    expect(areaLanding(dest("network"), {}).to).toBe("/network");
    // Backups opens at the copy of Noust itself, the one copy a hub can make (finding 21 of the audit).
    expect(areaLanding(dest("backups"), hub).to).toBe("/backups/noust");
    const bar = sidebarFor(hub)
      .flat()
      .map((entry) => entry.id);
    for (const gone of ["apps", "databases", "cron", "domains", "security", "logs"]) expect(bar, `a hub's bar offers ${gone}`).not.toContain(gone);
    for (const kept of ["overview", "catalog", "network", "alerts", "activity", "backups", "server"]) expect(bar, `a hub's bar lacks ${kept}`).toContain(kept);
  });

  it("opens Logs at the system's lines on a machine with no applications, and drops it where there is no journal either", () => {
    const noApps: NavContext = { features: (key) => key !== "applications" };
    expect(areaLanding(dest("logs"), noApps)).toEqual({ to: "/logs", search: { source: "system" } });
    const nothing: NavContext = { features: (key) => key !== "applications" && key !== "system_logs" };
    expect(areaOffered(dest("logs"), nothing)).toBe(false);
  });

  it("finds the entry a page belongs to, so the page of a section the server cannot do is known", () => {
    expect(entryAt("/network/tunnels")?.feature).toBe("tunnels");
    expect(entryAt("/databases/postgresql/clientes/data")?.feature).toBe("databases");
    expect(entryAt("/apps/shop.example.com/logs")?.feature).toBe("applications");
    expect(entryAt("/server/services/noust-web/logs")?.feature).toBe("services");
    expect(entryAt("/")?.feature).toBeUndefined();
  });
});
