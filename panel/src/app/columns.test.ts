import { describe, expect, it } from "vitest";

import { INTENT_ORDER, appColumn, columnKindOf, currentItem, serverColumn } from "./columns";

const everything = { staged: true } as const;

describe("which resource has a second column", () => {
  it("is an application's page, the server's, Databases and Settings: and nothing else", () => {
    expect(columnKindOf("/apps/shop.example.net")).toBe("app");
    expect(columnKindOf("/apps/shop.example.net/settings/source")).toBe("app");
    expect(columnKindOf("/server")).toBe("server");
    expect(columnKindOf("/server/services/noust-web")).toBe("server");
    expect(columnKindOf("/settings/tokens")).toBe("settings");
    for (const path of ["/apps", "/apps/new", "/domains/sites", "/fleet/apps", "/alerts", "/account"]) expect(columnKindOf(path), path).toBeNull();
    // Home has the panels in it, the server's and the fleet's.
    for (const path of ["/", "/fleet", "/fleet/"]) expect(columnKindOf(path), path).toBe("home");
  });

  it("has Databases' list, its database servers and one server's settings, and not a database's own page, which has its tabs", () => {
    for (const path of ["/databases", "/databases/", "/databases/servers", "/databases/engines/postgresql/settings"]) expect(columnKindOf(path), path).toBe("databases");
    for (const path of ["/databases/postgresql/shop", "/databases/postgresql/shop/connect", "/databases/postgresql@pg16/shop/data"]) expect(columnKindOf(path), path).toBeNull();
  });

  it("does not take the wizard that makes an application for one", () => {
    expect(columnKindOf("/apps/new")).toBeNull();
    expect(columnKindOf("/apps/newsletter.example.com")).toBe("app");
  });
});

describe("an application's sections, grouped by what they are for", () => {
  it("are use it, configure it, fix it and what is dangerous, in that order, from the register", () => {
    const groups = appColumn(everything, false);
    expect(groups.map((group) => group.id)).toEqual(["use", "configure", "fix", "danger"]);
    expect(groups.map((group) => group.id)).toEqual(INTENT_ORDER.slice());
    const byGroup = Object.fromEntries(groups.map((group) => [group.id, group.items.map((item) => item.id)]));
    expect(byGroup).toEqual({
      use: ["app", "app.deployments", "app.logs", "app.metrics"],
      configure: ["app.environment", "app.database", "app.domains", "app.settings"],
      fix: ["app.diagnose", "app.terminal", "app.files"],
      danger: ["app.settings.export", "app.settings.delete"],
    });
    expect(groups.every((group) => group.titled)).toBe(true);
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

describe("the machine's sections", () => {
  it("are one list with no titles: its status first, then the sections in the order a machine is gone through", () => {
    const [group, ...rest] = serverColumn(everything);
    expect(rest).toEqual([]);
    expect(group?.titled).toBe(false);
    expect(group?.items.map((item) => item.to)).toEqual([
      "/server",
      "/server/system",
      "/server/security",
      "/server/network",
      "/server/services",
      "/server/webserver",
      "/server/terminal",
      "/server/files",
      "/server/central",
    ]);
    expect(group?.items[0]?.exact).toBe(true);
  });

  it("offers the central's permissions only where a central manages the server", () => {
    const paths = (fleet: boolean) => serverColumn({ can: () => true, when: { fleet } }).flatMap((group) => group.items.map((item) => item.to));

    expect(paths(true)).toContain("/server/central");
    expect(paths(false)).not.toContain("/server/central");
    expect(paths(false)).toContain("/server/files");
  });

  it("leaves Terminal out for whoever may not open it", () => {
    const paths = serverColumn({ can: () => false }).flatMap((group) => group.items.map((item) => item.to));
    expect(paths).not.toContain("/server/terminal");
    expect(paths).toContain("/server/services");
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
});
