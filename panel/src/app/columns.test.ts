import { describe, expect, it } from "vitest";

import { VIEWS } from "../features/nav/views";
import { INTENT_ORDER, RESOURCE_PARENT, accountColumn, appColumn, areaColumn, columnKindOf, currentItem, depthOf, resourceColumn } from "./columns";
import type { ColumnGroup, ColumnKind } from "./columns";
import { DESTS, settingsGroups } from "./nav";

const everything = { staged: true } as const;

/** A route's path with every parameter filled in: an address a person could open. */
const address = (pattern: string): string => pattern.replace(/\$\w+/g, "sample");

/** The areas whose column is a body of its own (Home's, Databases' engines and Settings' filter), not the register's list. */
const OWN_BODY: ReadonlySet<string> = new Set(["overview", "databases", "settings"]);

const AREAS = DESTS.filter((entry) => entry.kind === "area");

describe("which column a path has", () => {
  it("is its area's, so every address of the console has one", () => {
    expect(columnKindOf("/")).toBe("overview");
    expect(columnKindOf("/apps")).toBe("apps");
    expect(columnKindOf("/apps/new")).toBe("apps");
    expect(columnKindOf("/projects")).toBe("apps");
    expect(columnKindOf("/projects/teams")).toBe("apps");
    expect(columnKindOf("/projects/acme/web")).toBe("apps");
    expect(columnKindOf("/catalog/installed")).toBe("catalog");
    expect(columnKindOf("/catalog/nextcloud")).toBe("catalog");
    expect(columnKindOf("/cron")).toBe("cron");
    expect(columnKindOf("/domains/sites/example.com")).toBe("domains");
    expect(columnKindOf("/domains/dns/zones/example.com")).toBe("domains");
    expect(columnKindOf("/logs")).toBe("logs");
    expect(columnKindOf("/alerts/probes")).toBe("alerts");
    expect(columnKindOf("/activity")).toBe("activity");
    expect(columnKindOf("/backups/volumes")).toBe("backups");
    expect(columnKindOf("/server")).toBe("server");
    expect(columnKindOf("/server/files")).toBe("server");
    expect(columnKindOf("/databases")).toBe("databases");
    expect(columnKindOf("/databases/servers")).toBe("databases");
    expect(columnKindOf("/databases/engines/postgresql/settings")).toBe("databases");
    expect(columnKindOf("/settings/tokens")).toBe("settings");
    expect(columnKindOf("/integrations/github/callback")).toBe("settings");
    expect(columnKindOf("/account/security")).toBe("account");
  });

  it("is the area's on the fleet's pages, which are the faces of areas", () => {
    expect(columnKindOf("/fleet")).toBe("overview");
    expect(columnKindOf("/fleet/apps")).toBe("apps");
    expect(columnKindOf("/fleet/placements")).toBe("apps");
    expect(columnKindOf("/fleet/certificates")).toBe("domains");
    expect(columnKindOf("/fleet/backups")).toBe("backups");
    expect(columnKindOf("/fleet/servers")).toBe("server");
    expect(columnKindOf("/fleet/updates")).toBe("server");
    expect(columnKindOf("/fleet/activity")).toBe("activity");
    expect(columnKindOf("/fleet/alerts")).toBe("alerts");
    expect(columnKindOf("/fleet/jobs/42")).toBe("activity");
  });

  it("is the resource's where one is entered: an application, a database, a service, a team", () => {
    expect(columnKindOf("/apps/shop.example.net")).toBe("app");
    expect(columnKindOf("/apps/shop.example.net/settings/source")).toBe("app");
    expect(columnKindOf("/apps/shop.example.net/deployments/12")).toBe("app");
    expect(columnKindOf("/databases/postgresql/shop")).toBe("database");
    expect(columnKindOf("/databases/postgresql@pg16/shop/data")).toBe("database");
    expect(columnKindOf("/server/services/noust-web")).toBe("service");
    expect(columnKindOf("/server/services/noust-web/logs")).toBe("service");
    expect(columnKindOf("/server/services/noust-web/unit")).toBe("service");
    expect(columnKindOf("/teams/acme")).toBe("team");
    expect(columnKindOf("/teams/acme/members")).toBe("team");
  });

  it("does not take the wizard that makes an application for an application, nor a database server for a database", () => {
    expect(columnKindOf("/apps/new")).toBe("apps");
    expect(columnKindOf("/apps/newsletter.example.com")).toBe("app");
    expect(columnKindOf("/databases/servers")).toBe("databases");
    expect(columnKindOf("/databases/engines/redis/settings")).toBe("databases");
  });

  it("is none for an address under no area", () => {
    expect(columnKindOf("/nothing-here")).toBeNull();
  });

  it("has a column for every area of the register (NV-22)", () => {
    expect(AREAS.length).toBeGreaterThanOrEqual(11);
    for (const area of AREAS) expect(columnKindOf(area.to), area.id).toBe(area.id);
    for (const area of AREAS) expect(columnKindOf(area.fleetTo ?? area.to), `${area.id} at its fleet face`).toBe(area.id);
  });

  it("slides one level deeper to a resource, from the area it is entered from", () => {
    expect(depthOf("apps")).toBe(0);
    expect(depthOf("app")).toBe(1);
    for (const [resource, area] of Object.entries(RESOURCE_PARENT)) {
      expect(depthOf(resource as ColumnKind), resource).toBe(1);
      expect(depthOf(area), area).toBe(0);
      expect(AREAS.some((entry) => entry.id === area), `${resource} is entered from ${area}, which is no area`).toBe(true);
    }
  });
});

/** Every column the register can draw, with its entries: what the rule of NV-24 walks. */
function columns(): { name: string; kind: ColumnKind; groups: ColumnGroup[] }[] {
  const lists: { name: string; kind: ColumnKind; groups: ColumnGroup[] }[] = [
    { name: "app", kind: "app", groups: appColumn(everything, false) },
    { name: "database", kind: "database", groups: resourceColumn("database", everything) },
    { name: "service", kind: "service", groups: resourceColumn("service", everything) },
    { name: "team", kind: "team", groups: resourceColumn("team", everything) },
    { name: "account", kind: "account", groups: accountColumn(everything) },
  ];
  for (const area of AREAS) {
    if (OWN_BODY.has(area.id)) continue;
    lists.push({ name: area.id, kind: area.id as ColumnKind, groups: areaColumn(area.id as ColumnKind, everything, "server") });
    if (DESTS.some((entry) => entry.scope === "fleet" && entry.faceOf === area.id)) lists.push({ name: `${area.id} on the fleet`, kind: area.id as ColumnKind, groups: areaColumn(area.id as ColumnKind, everything, "fleet") });
  }
  return lists;
}

describe("every area and every resource has sections to list", () => {
  it("lists at least one entry, in a column of the register or of the area's views", () => {
    for (const { name, groups } of columns()) expect(groups.flatMap((group) => group.items).length, name).toBeGreaterThan(0);
  });

  it("gives every entry an icon, a name and a path that is a route", () => {
    for (const { name, groups } of columns())
      for (const item of groups.flatMap((group) => group.items)) {
        expect(item.icon, `${name}: ${item.id}`).toBeDefined();
        expect(item.label, `${name}: ${item.id}`).not.toBe("");
        expect(item.to.startsWith("/"), `${name}: ${item.id}`).toBe(true);
      }
  });

  it("keeps a column to about sixteen entries, and an application's to seventeen at the most (NV-3)", () => {
    for (const { name, kind, groups } of columns()) {
      const count = groups.flatMap((group) => group.items).length;
      expect(count, name).toBeLessThanOrEqual(kind === "app" ? 17 : 16);
    }
  });
});

describe("no entry of a column leaves its column (NV-24)", () => {
  it("resolves every entry, with its parameters filled in, to a route under the column it is listed in", () => {
    const strays: string[] = [];
    for (const { name, kind, groups } of columns())
      for (const item of groups.flatMap((group) => group.items)) {
        const found = columnKindOf(address(item.to));
        if (found !== kind) strays.push(`${name}: ${item.id} (${item.to}) is under the column of ${String(found)}`);
      }
    expect(strays).toEqual([]);
  });

  it("holds for the sections of Settings, whichever group they are in", () => {
    for (const group of settingsGroups(everything))
      for (const entry of group.sections) expect(columnKindOf(address(entry.to)), entry.id).toBe("settings");
  });

  it("holds for the fixed views of an area that is one page: they are its own address with a search", () => {
    for (const [area, views] of Object.entries(VIEWS)) {
      const entry = DESTS.find((candidate) => candidate.id === area);
      expect(entry?.kind, area).toBe("area");
      expect(views.length, area).toBeGreaterThan(1);
      // The first is the page as it opens, the others are presets of its own filters.
      expect(views[0]?.search, area).toBeUndefined();
      for (const view of views.slice(1)) expect(Object.keys(view.search ?? {}).length, `${area}: ${view.id}`).toBeGreaterThan(0);
    }
  });
});

describe("an application's sections, grouped by what they are for", () => {
  it("are use it, configure it, fix it, move it and what is dangerous, in that order, from the register", () => {
    const groups = appColumn(everything, false);
    expect(groups.map((group) => group.id)).toEqual(INTENT_ORDER.slice());
    const byGroup = Object.fromEntries(groups.map((group) => [group.id, group.items.map((item) => item.id)]));
    expect(byGroup).toEqual({
      use: ["app", "app.deployments", "app.logs", "app.metrics"],
      configure: ["app.environment", "app.database", "app.domains", "app.settings"],
      fix: ["app.diagnose", "app.terminal", "app.files"],
      move: ["app.settings.export"],
      danger: ["app.settings.delete"],
    });
    expect(groups.every((group) => group.heading !== undefined)).toBe(true);
    expect(groups.find((group) => group.id === "danger")?.danger).toBe(true);
  });

  it("does not call taking an application to another server dangerous (3.4): it is the group of moving it", () => {
    const danger = appColumn(everything, false).find((group) => group.id === "danger");
    expect(danger?.items.map((item) => item.id)).toEqual(["app.settings.delete"]);
  });

  it("leaves what a role cannot use out, rather than disabling it", () => {
    const ids = appColumn({ can: (permission) => permission !== "apps.terminal" }, false).flatMap((group) => group.items.map((item) => item.id));
    expect(ids).not.toContain("app.terminal");
    expect(ids).not.toContain("app.files");
    expect(ids).toContain("app.diagnose");
  });

  it("shows an adopted site what it has: its state, its logs, its use of the machine, what to fix and Delete", () => {
    const ids = appColumn(everything, true).flatMap((group) => group.items.map((item) => item.id));
    expect(ids).toEqual(["app", "app.logs", "app.metrics", "app.settings", "app.diagnose", "app.terminal", "app.files", "app.settings.delete"]);
  });

  it("gives every section an icon and the Overview an exact path", () => {
    const items = appColumn(everything, false).flatMap((group) => group.items);
    for (const item of items) expect(item.icon, item.id).toBeDefined();
    expect(items.find((item) => item.id === "app")?.exact).toBe(true);
    expect(items.find((item) => item.id === "app.logs")?.exact).toBe(false);
  });
});

describe("the column of an area", () => {
  it("lists the area's own page first, named for what it is there, then its sections", () => {
    const [head] = areaColumn("alerts", everything);
    expect(head?.items.map((item) => item.to)).toEqual(["/alerts", "/alerts/checks", "/alerts/probes", "/alerts/status", "/alerts/metrics"]);
    expect(head?.items[0]?.exact).toBe(true);
    const [backups] = areaColumn("backups", everything);
    expect(backups?.items.map((item) => item.to)).toEqual(["/backups", "/backups/schedules", "/backups/volumes", "/backups/noust", "/backups/destinations"]);
    const [catalog] = areaColumn("catalog", everything);
    expect(catalog?.items.map((item) => item.to)).toEqual(["/catalog", "/catalog/installed"]);
  });

  it("lists the fixed views of an area that is one page, in the place of its own page", () => {
    const [logs] = areaColumn("logs", everything);
    expect(logs?.items.map((item) => [item.to, item.search])).toEqual([
      ["/logs", undefined],
      ["/logs", { source: "system" }],
    ]);
    const [activity] = areaColumn("activity", everything);
    // The views, then the tasks the server runs (a page of its own, no view of this one).
    expect(activity?.items.map((item) => item.search)).toEqual([undefined, { kind: "deployments" }, { kind: "access" }, undefined]);
    expect(activity?.items.at(-1)?.to).toBe("/activity/tasks");
  });

  it("leaves out what a role cannot see, and Projects where nobody organised theirs", () => {
    const paths = (organised: boolean) => areaColumn("apps", { staged: true, when: { organised } }).flatMap((group) => group.items.map((item) => item.to));
    expect(paths(true)).toEqual(["/apps", "/projects", "/projects/teams"]);
    expect(paths(false)).toEqual(["/apps"]);
  });

  it("draws the fleet's faces of an area from All servers, and none of the server's own pages", () => {
    const paths = (kind: ColumnKind) => areaColumn(kind, everything, "fleet").flatMap((group) => group.items.map((item) => item.to));
    expect(paths("apps")).toEqual(["/fleet/apps", "/fleet/placements"]);
    expect(paths("domains")).toEqual(["/fleet/certificates"]);
    expect(paths("server")).toEqual(["/fleet/servers", "/fleet/updates", "/fleet/software"]);
    expect(paths("backups")).toEqual(["/fleet/backups"]);
  });
});

describe("a resource's sections", () => {
  it("are the resource's page and its tabs, in the register's order", () => {
    expect(resourceColumn("service", everything)[0]?.items.map((item) => item.to)).toEqual(["/server/services/$name", "/server/services/$name/logs"]);
    expect(resourceColumn("team", everything)[0]?.items.map((item) => item.to)).toEqual([
      "/teams/$team",
      "/teams/$team/members",
      "/teams/$team/variables",
      "/teams/$team/notifications",
      "/teams/$team/settings",
    ]);
  });

  it("depend on what a database's engine can do, never on its name", () => {
    const paths = (capabilities: readonly string[]) => resourceColumn("database", { capabilities })[0]?.items.map((item) => item.to);
    expect(paths(["tables", "sql", "dump", "users", "metrics"])).toContain("/databases/$engine/$name/query");
    expect(paths(["keys", "dump", "metrics"])).not.toContain("/databases/$engine/$name/query");
    expect(paths(["keys", "dump"])).toContain("/databases/$engine/$name/data");
  });

  it("are the account's two: its profile and the security of its sign-in", () => {
    expect(accountColumn(everything)[0]?.items.map((item) => item.to)).toEqual(["/account", "/account/security"]);
  });
});

describe("the item a page is in", () => {
  const items = [
    { to: "/apps/$domain", exact: true },
    { to: "/apps/$domain/deployments" },
    { to: "/apps/$domain/settings" },
    { to: "/apps/$domain/settings/export" },
  ];

  it("is the one whose path is the longest start of the route, so only one is current", () => {
    expect(currentItem(items, "/apps/$domain/settings/export")?.to).toBe("/apps/$domain/settings/export");
    expect(currentItem(items, "/apps/$domain/settings/source")?.to).toBe("/apps/$domain/settings");
    expect(currentItem(items, "/apps/$domain/deployments/$id")?.to).toBe("/apps/$domain/deployments");
  });

  it("keeps an exact item from staying lit beneath its own path, and reads an index route's trailing slash", () => {
    expect(currentItem(items, "/apps/$domain/")?.to).toBe("/apps/$domain");
    expect(currentItem(items, "/apps/$domain/logs")).toBeUndefined();
  });

  it("is none for a route the list does not hold", () => {
    expect(currentItem(items, "/databases")).toBeUndefined();
    expect(currentItem<{ to: string }>([], "/apps/$domain")).toBeUndefined();
  });

  it("tells the views of one page apart by the search they set, the plain one being the page as it opens", () => {
    const views = [{ to: "/logs", exact: true }, { to: "/logs", exact: true, search: { source: "system" } }];
    expect(currentItem(views, "/logs", {})?.search).toBeUndefined();
    expect(currentItem(views, "/logs", { q: "timeout" })?.search).toBeUndefined();
    expect(currentItem(views, "/logs", { source: "system" })?.search).toEqual({ source: "system" });
    expect(currentItem(views, "/logs", { source: "system", unit: "nginx.service" })?.search).toEqual({ source: "system" });
  });
});
