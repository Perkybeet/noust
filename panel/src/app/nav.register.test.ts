import { QueryClient } from "@tanstack/react-query";
import { describe, expect, it } from "vitest";

import { SETTINGS_SECTIONS } from "../features/app/settings/sections";
import { APPS_SCREENS } from "../features/nav/apps";
import { BACKUPS_SCREENS } from "../features/nav/backups";
import { APPS_TABS } from "../features/nav/appsTabs";
import { DATABASES_SCREENS, DATABASE_SCREENS } from "../features/nav/databases";
import { DOMAINS_SCREENS } from "../features/nav/domains";
import { OVERVIEW_SCREENS } from "../features/nav/overview";
import { PROJECTS_SCREENS } from "../features/nav/projects";
import { SERVER_SCREENS, SERVICE_SCREENS } from "../features/nav/server";
import * as en from "../i18n/en";
import type { PlainKey } from "../i18n";
import { filterCommands } from "./CommandPalette";
import type { Command } from "./CommandPalette";
import {
  APP_TABS,
  DESTS,
  EXCEPTIONS,
  FLEET_APPS_SCREENS,
  INTERIM,
  PLANNED,
  REDIRECTS,
  RENAMED,
  SETTINGS_GROUPS,
  TEAM_TABS,
  crumbsOf,
  dest,
  paletteEntries,
  settingsGroups,
  shortcuts,
  sidebarFor,
  tabsById,
  tabsOf,
  withCounts,
} from "./nav";
import type { Dest, NavContext, PaletteContext, Strip } from "./nav";
import { ICON_IDS, iconOf } from "../features/nav/icons";
import { CENTRAL_PATHS } from "./nodeRoute";
import { buildRouter } from "./router";

/**
 * The register is held to the router, in both directions, and to the rules of navigation that a
 * machine can check (docs/DESIGN.md, NV-1 to NV-14), the way `permissions` and `scopes` hold the
 * API's routes. The seven checks of the design (2.8) are the seven `describe`s that start with a
 * number.
 */

/** English text of a catalog key, as the register's labels are read. */
function text(key: string, catalog: object = en): string {
  let node: unknown = catalog;
  for (const part of key.split(".")) node = (node as Record<string, unknown> | undefined)?.[part];
  if (typeof node !== "string") throw new Error(`the catalog has no text for "${key}"`);
  return node;
}

const label = (entry: Dest): string => text(entry.label);

const ROUTES: readonly string[] = Object.keys(buildRouter(new QueryClient()).routesByPath).sort();

const LIVE_SOURCES: ReadonlySet<string> = new Set(REDIRECTS.filter((row) => row.state === "live").map((row) => row.from));

const EVERYTHING: NavContext = { staged: true };

const paths = (tabs: readonly { to: string }[]): string[] => tabs.map((tab) => tab.to);

function paletteContext(over: Partial<PaletteContext> = {}): PaletteContext {
  return { t: (key: PlainKey) => text(key), can: () => true, ...over };
}

describe("1. every route has an entry, or a reason", () => {
  it("covers every route of the router", () => {
    const entered = new Set<string>(DESTS.map((entry) => entry.to));
    const uncovered = ROUTES.filter((route) => !entered.has(route) && !(route in EXCEPTIONS) && !LIVE_SOURCES.has(route));
    expect(uncovered, "a route with no entry in app/nav.ts (or a reason in EXCEPTIONS)").toEqual([]);
  });

  it("has a reason for each exception, and each exception is a route nobody lists", () => {
    const entered = new Set<string>(DESTS.map((entry) => entry.to));
    for (const [route, reason] of Object.entries(EXCEPTIONS)) {
      expect(ROUTES, `${route} is an exception but not a route`).toContain(route);
      expect(entered.has(route), `${route} is an exception and has an entry`).toBe(false);
      expect(reason.trim(), `${route} has no reason`).not.toBe("");
    }
  });

  it("waits for the routes it planned, and moves them into the register when they exist", () => {
    for (const planned of PLANNED) {
      expect(ROUTES, `${planned.to} exists now: move "${planned.id}" from PLANNED into DESTS`).not.toContain(planned.to);
    }
  });
});

describe("2. every entry is a route", () => {
  it("has a route for each entry, once, and a parent that is an entry", () => {
    const ids = new Set<string>();
    for (const entry of DESTS) {
      expect(ids.has(entry.id), `${entry.id} is declared twice`).toBe(false);
      ids.add(entry.id);
      expect(ROUTES, `${entry.id} is at ${entry.to}, which is not a route`).toContain(entry.to);
    }
    for (const entry of DESTS) {
      if (entry.parent !== undefined) expect(ids.has(entry.parent), `${entry.id} is under ${entry.parent}, which is not an entry`).toBe(true);
    }
  });

  it("gives one path to two entries only when what the resource can do tells them apart", () => {
    const byPath = new Map<string, Dest[]>();
    for (const entry of DESTS) byPath.set(entry.to, [...(byPath.get(entry.to) ?? []), entry]);
    for (const [path, entries] of byPath) {
      if (entries.length < 2) continue;
      const needs = entries.flatMap((entry) => entry.needs ?? []);
      expect(entries.every((entry) => entry.needs !== undefined), `${path} is two screens`).toBe(true);
      expect(new Set(needs).size, `${path}: the capabilities of its entries overlap`).toBe(needs.length);
    }
  });

  it("is a tree: every chain of parents ends", () => {
    for (const entry of DESTS) expect(crumbsOf(entry.id).length, entry.id).toBeLessThanOrEqual(4);
  });

  it("names where the screens reached from nowhere are reached from", () => {
    const ids = new Set(DESTS.map((entry) => entry.id));
    for (const entry of DESTS.filter((candidate) => candidate.kind === "hidden")) {
      expect(entry.reachedFrom, `${entry.id} is in no list and says nowhere it is reached from (NV-13)`).toBeDefined();
      expect(ids.has(entry.reachedFrom ?? ""), `${entry.id} is reached from ${entry.reachedFrom}, which is not an entry`).toBe(true);
    }
  });
});

describe("3. the budgets", () => {
  it("keeps every URL to four segments after the server (NV-2)", () => {
    for (const route of ROUTES) expect(route.split("/").filter((segment) => segment !== "").length, route).toBeLessThanOrEqual(4);
  });

  it("keeps a tab strip to eight tabs (NV-3)", () => {
    const strips = new Set(DESTS.filter((entry) => entry.kind === "tab").map((entry) => entry.parent ?? ""));
    for (const strip of strips) expect(tabsById(strip, EVERYTHING).length, `the strip of ${strip}`).toBeLessThanOrEqual(8);
  });

  it("keeps Settings to eight sections a group, six groups and twenty a block (NV-3)", () => {
    for (const block of ["server", "instance"] as const) {
      const groups = settingsGroups(block, EVERYTHING);
      expect(groups.length, block).toBeLessThanOrEqual(6);
      expect(groups.flatMap((group) => group.sections).length, block).toBeLessThanOrEqual(20);
      for (const group of groups) expect(group.sections.length, `${block} / ${group.id}`).toBeLessThanOrEqual(8);
    }
  });

  it("puts every settings section in exactly one group of the groups there are", () => {
    const grouped = (["server", "instance"] as const).flatMap((block) => settingsGroups(block, EVERYTHING).flatMap((group) => group.sections.map((entry) => entry.id)));
    expect(new Set(grouped).size, "a section is in two groups").toBe(grouped.length);
    for (const block of ["server", "instance"] as const) {
      for (const group of settingsGroups(block, EVERYTHING)) expect(SETTINGS_GROUPS[block], group.id).toContain(group.id);
    }
    const sections = DESTS.filter((entry) => entry.id === "settings" || (entry.parent === "settings" && entry.kind === "section"));
    // The account's own sections are not Settings (NV-7): they live at /account, and every section of Settings is in a group.
    expect(sections.filter((entry) => !grouped.includes(entry.id)).map((entry) => entry.id)).toEqual([]);
  });

  it("keeps the bar to ten destinations on a server and seven on a fleet, in three groups (NV-3)", () => {
    const server = sidebarFor("server");
    expect(server.length).toBeLessThanOrEqual(3);
    expect(server.flat().length).toBeLessThanOrEqual(10);
    expect(sidebarFor("fleet").flat().length).toBeLessThanOrEqual(7);
  });

  it("draws the bar of 4.0: what runs, what happens, the machine; and the fleet's seven (2.3)", () => {
    expect(sidebarFor("server").map((group) => group.map((entry) => entry.to))).toEqual([
      ["/", "/projects", "/apps", "/databases", "/domains", "/backups", "/cron"],
      ["/logs", "/activity"],
      ["/server"],
    ]);
    expect(sidebarFor("fleet").map((group) => group.map((entry) => entry.to))).toEqual([
      ["/fleet", "/fleet/servers", "/fleet/apps", "/fleet/certificates", "/fleet/backups", "/fleet/updates", "/fleet/activity"],
    ]);
    // The catalog left the bar (D-2): it is the second and third view of Applications, at the addresses it always had.
    expect(sidebarFor("server").flat().map((entry) => entry.id)).not.toContain("catalog");
    expect(paths(tabsOf(APPS_SCREENS))).toEqual(["/apps", "/catalog", "/catalog/installed"]);
    // What the list and the catalogue draw is the same three, without the wizard the register also names.
    expect(paths(tabsOf(APPS_TABS))).toEqual(["/apps", "/catalog", "/catalog/installed"]);
    expect(dest("apps.recipes").parent).toBe("apps");
  });

  it("keeps a screen's place in the bar apart from where it is reached from (NV-4)", () => {
    // Settings is pinned under the bar and the fleet is offered above it: neither is one of its groups.
    expect(sidebarFor("server").flat().map((entry) => entry.id)).not.toContain("settings");
    expect(sidebarFor("server").flat().map((entry) => entry.id)).not.toContain("fleet");
  });
});

describe("4. the names", () => {
  it("says a destination of the bar in a word or two, without 'and' (NV-5)", () => {
    const withAnd = [...sidebarFor("server").flat(), ...sidebarFor("fleet").flat()].filter((entry) => /\band\b/i.test(label(entry))).map((entry) => entry.id);
    expect(withAnd, "a bar name with 'and'").toEqual([]);
    expect(label(dest("domains"))).toBe("Domains");
  });

  it("never gives two destinations of one scope the same name", () => {
    for (const scope of ["server", "fleet"] as const) {
      const names = [...sidebarFor(scope).flat(), ...DESTS.filter((entry) => entry.scope === "both" && entry.kind === "area")].map(label);
      expect(new Set(names).size, `${scope}: ${names.join(", ")}`).toBe(names.length);
    }
  });

  it("never gives two tabs of one strip the same name", () => {
    const strips = new Set(DESTS.filter((entry) => entry.kind === "tab").map((entry) => entry.parent ?? ""));
    for (const strip of strips) {
      // A database has Data or Keys, never both: its tabs are checked for the engine that has the most.
      const names = tabsById(strip, { staged: true, capabilities: ["tables", "sql", "dump", "users", "profiles", "metrics"] }).map((tab) => text(tab.label));
      expect(new Set(names).size, `the strip of ${strip}: ${names.join(", ")}`).toBe(names.length);
    }
  });

  it("has no screen called Advanced (NV-12)", () => {
    for (const entry of DESTS) expect(label(entry).toLowerCase(), entry.id).not.toBe("advanced");
  });

  it("draws every area and every section of a list on the side as the object it is, and no two of one list as the same one (NV-19, I-5)", () => {
    // The table of features/nav/icons.ts has the areas and the sections, and nothing else: a tab has no icon of its own.
    // Plus General, the first entry of an application's settings: the tab they are under, drawn as the head of the list.
    const drawn = [...DESTS.filter((entry) => entry.kind === "area" || entry.kind === "section").map((entry) => entry.id), "app.settings"];
    expect([...ICON_IDS].sort(), "the table has an object for every area and section, and nothing more").toEqual([...drawn].sort());
    for (const id of drawn) expect(iconOf({ id }).displayName, `${id} has no icon of lucide's`).toBeDefined();
    // A list is the sidebar of a scope, or the sections under one entry.
    const lists = new Map<string, Dest[]>();
    for (const entry of DESTS) {
      const key = entry.kind === "area" ? `bar of ${entry.scope}` : entry.kind === "section" ? `under ${entry.parent ?? ""}` : "";
      if (key !== "") lists.set(key, [...(lists.get(key) ?? []), entry]);
    }
    for (const [key, entries] of lists) {
      // The first entry of a list on the side is the entry it is under (General, the account's profile): it is drawn too.
      const parent = key.startsWith("under ") ? DESTS.find((entry) => entry.id === key.slice(6)) : undefined;
      const inList = [...(parent !== undefined && parent.kind !== "hidden" ? [parent] : []), ...entries];
      const names = inList.map((entry) => iconOf(entry).displayName ?? "");
      expect(new Set(names).size, `${key}: ${inList.map((entry) => `${entry.id}=${iconOf(entry).displayName ?? "?"}`).join(", ")}`).toBe(names.length);
    }
  });

  it("gives a screen that is only a tab the icon of its area in the palette", () => {
    const entries = paletteEntries({ t: (key) => key, can: () => true, when: { organised: true, fleet: true } });
    const server = entries.find((entry) => entry.id === "server");
    const updates = entries.find((entry) => entry.id === "server.updates");
    expect(updates?.icon).toBe(server?.icon);
    const alerts = entries.find((entry) => entry.id === "settings.alerts");
    expect(alerts?.icon).not.toBe(entries.find((entry) => entry.id === "settings")?.icon);
  });

  it("names every screen in Spanish too", async () => {
    const es = await import("../i18n/es");
    for (const entry of DESTS) {
      expect(text(entry.label, es), entry.id).not.toBe("");
      if (entry.keywords !== undefined) expect(text(entry.keywords, es), `${entry.id} keywords`).not.toBe("");
      if (entry.command !== undefined) expect(text(entry.command, es), `${entry.id} command`).not.toBe("");
    }
  });
});

describe("5. the shortcuts", () => {
  it("gives every destination of the bar two keys, in both scopes (NV-6)", () => {
    const without = [...sidebarFor("server").flat(), ...sidebarFor("fleet").flat(), dest("settings")].filter((entry) => entry.shortcut === undefined).map((entry) => entry.id);
    expect(without, "a destination of the bar with no shortcut").toEqual([]);
  });

  it("keeps the same letter for the same noun on a server's bar and on the fleet's", () => {
    const letter = (id: string): string => (dest(id).shortcut ?? []).join(" ");
    expect(letter("fleet.apps")).toBe(letter("apps"));
    expect(letter("fleet.backups")).toBe(letter("backups"));
    expect(letter("fleet.certificates")).toBe(letter("domains"));
    expect(letter("fleet.servers")).toBe(letter("server"));
    expect(letter("fleet")).toBe(letter("overview"));
  });

  it("never gives a letter to two destinations that can be on one bar, nor takes the ones the shell keeps", () => {
    const reserved = [["g", "d"], ["?"], ["/"]].map((keys) => keys.join(" "));
    const taken = new Map<string, string>();
    for (const entry of DESTS.filter((candidate) => candidate.shortcut !== undefined)) {
      const keys = (entry.shortcut ?? []).join(" ");
      expect(reserved, `${entry.id} takes ${keys}, which the shell keeps`).not.toContain(keys);
      const sameBar = [...taken].find(([other]) => other.startsWith(`${keys}@`) && (entry.scope === "both" || other.endsWith(`@both`) || other.endsWith(`@${entry.scope}`)));
      expect(sameBar?.[1], `${entry.id} and ${sameBar?.[1] ?? ""} both take ${keys}`).toBeUndefined();
      taken.set(`${keys}@${entry.scope}`, entry.id);
    }
  });

  it("lists those of the scope on screen, in the order of the bar", () => {
    expect(shortcuts("server").map((shortcut) => [...shortcut.keys, shortcut.to])).toEqual([
      ["g", "o", "/"],
      ["g", "p", "/projects"],
      ["g", "a", "/apps"],
      ["g", "b", "/databases"],
      ["g", "n", "/domains"],
      ["g", "k", "/backups"],
      ["g", "c", "/cron"],
      ["g", "l", "/logs"],
      ["g", "y", "/activity"],
      ["g", "v", "/server"],
      ["g", "s", "/settings"],
    ]);
    expect(shortcuts("fleet").map((shortcut) => [...shortcut.keys, shortcut.to])).toEqual([
      ["g", "o", "/fleet"],
      ["g", "v", "/fleet/servers"],
      ["g", "a", "/fleet/apps"],
      ["g", "n", "/fleet/certificates"],
      ["g", "k", "/fleet/backups"],
      ["g", "u", "/fleet/updates"],
      ["g", "y", "/fleet/activity"],
      ["g", "s", "/settings"],
    ]);
  });
});

describe("6. the palette finds every screen", () => {
  const entries = paletteEntries(paletteContext());
  const commands: Command[] = entries.map((entry) => ({
    id: entry.id,
    group: entry.id === "settings" || entry.id.startsWith("settings.") ? "Settings" : "Pages",
    label: entry.text,
    keywords: entry.keywords,
    detail: entry.path,
    kind: "navigate",
    run: () => undefined,
  }));
  const found = (query: string): string[] => filterCommands(commands, query).flatMap((group) => group.items.map((item) => item.id));

  it("offers every screen of a path of no parameters that a person may open", () => {
    const expected = DESTS.filter((entry) => !entry.to.includes("$") && !INTERIM.some((row) => row.from === entry.to)).map((entry) => entry.id);
    expect(entries.map((entry) => entry.id)).toEqual(expected);
  });

  it("finds each one first by its own name", () => {
    for (const entry of entries) {
      const first = found(entry.text)[0];
      expect(first, `"${entry.text}" finds ${first ?? "nothing"} before ${entry.id}`).toBe(entry.id);
    }
  });

  it("finds each one by every word it is known by, the old names included", () => {
    for (const entry of entries) {
      for (const word of entry.keywords.split(/\s+/).filter((candidate) => candidate.length > 1)) {
        expect(found(word), `"${word}" does not find ${entry.id}`).toContain(entry.id);
      }
    }
  });

  it("says where each one lives, in words", () => {
    const byId = new Map(entries.map((entry) => [entry.id, entry.path]));
    expect(byId.get("server.updates")).toBe("Server › System updates");
    expect(byId.get("settings.alerts")).toBe("Settings › Alert rules");
    expect(byId.get("backups.schedules")).toBe("Backups › Schedules");
    expect(byId.get("server.terminal")).toBe("Server › Terminal");
    expect(byId.get("overview.map")).toBe("Overview › Map");
    for (const entry of DESTS.filter((candidate) => candidate.parent !== undefined && !candidate.to.includes("$") && !isInterim(candidate))) {
      expect(byId.get(entry.id)?.split(" › ").length, entry.id).toBeGreaterThanOrEqual(2);
    }
  });

  it("keeps the old name of a renamed screen as a word of its own", () => {
    const everything = paletteEntries(paletteContext({ staged: true }));
    for (const [id, keys] of Object.entries(RENAMED)) {
      const entry = everything.find((candidate) => candidate.id === id);
      expect(entry, `${id} is renamed but is not a screen the palette can open`).toBeDefined();
      for (const key of keys) expect(entry?.keywords, `${id} lost its old name`).toContain(text(key));
    }
  });
});

describe("7. what a role cannot see is not found", () => {
  // A screen of a resource (an application's Terminal) is offered by the page of that resource, not by the palette alone.
  const permissions = [...new Set(DESTS.flatMap((entry) => (entry.permission !== undefined && !entry.to.includes("$") ? [entry.permission] : [])))];

  it("leaves out of the palette what needs a permission the principal lacks", () => {
    expect(permissions.length).toBeGreaterThan(3);
    for (const permission of permissions) {
      const ids = paletteEntries(paletteContext({ can: (held) => held !== permission, staged: true })).map((entry) => entry.id);
      const needing = DESTS.filter((entry) => entry.permission === permission && !entry.to.includes("$")).map((entry) => entry.id);
      expect(needing.length, permission).toBeGreaterThan(0);
      for (const id of needing) expect(ids, `${id} needs ${permission}`).not.toContain(id);
    }
    const none = paletteEntries(paletteContext({ can: () => false })).map((entry) => entry.id);
    expect(none.filter((id) => dest(id).permission !== undefined)).toEqual([]);
    expect(none.length, "what needs no permission stays").toBeGreaterThan(10);
  });

  it("leaves a tab out of its strip, rather than disabling it", () => {
    expect(tabsById("domains", { can: () => false }).map((tab) => tab.to)).toEqual(["/domains", "/domains/sites"]);
    expect(tabsById("domains", { can: () => true }).map((tab) => tab.to)).toContain("/domains/dns");
  });

  it("leaves the destinations that do not apply out of the lists", () => {
    expect(sidebarFor("server", { when: { organised: false } }).flat().map((entry) => entry.id)).not.toContain("projects");
    expect(sidebarFor("server", { when: { organised: true } }).flat().map((entry) => entry.id)).toContain("projects");
  });
});

describe("the screens that are only a route so far", () => {
  it("are not offered by any list, and open the screen that holds the same thing", () => {
    for (const row of INTERIM) {
      const entry = DESTS.find((candidate) => candidate.to === row.from);
      expect(entry, `${row.from} has no entry`).toBeDefined();
      expect(sidebarFor("server").flat().map((candidate) => candidate.to)).not.toContain(row.from);
      expect(paletteEntries(paletteContext()).map((candidate) => candidate.to)).not.toContain(row.from);
      expect(DESTS.some((candidate) => candidate.to === row.to), `${row.from} opens ${row.to}, which is no screen`).toBe(true);
      expect(row.after.trim()).not.toBe("");
    }
  });

  it("are the central's, as the pages they stand for are", () => {
    for (const row of INTERIM) {
      const entry = DESTS.find((candidate) => candidate.to === row.from);
      expect(entry?.central, row.from).toBe(true);
    }
  });
});

describe("whose a screen is", () => {
  it("says whose every section of Settings is (NV-7)", () => {
    for (const entry of DESTS.filter((candidate) => candidate.id === "settings" || (candidate.parent === "settings" && candidate.kind === "section"))) {
      expect(entry.owner, `${entry.id} has no owner`).toBeDefined();
    }
  });

  it("puts the account's own sections with the account", () => {
    expect(dest("account").owner).toBe("account");
    expect(dest("account.security").owner).toBe("account");
  });

  it("holds the groups of 2.5 of the navigation design, in the order of 2.5", () => {
    const names = (block: "server" | "instance") => settingsGroups(block, EVERYTHING).map((group) => [group.id, group.sections.map((entry) => entry.id.replace(/^settings\.?/, "") || "general")]);
    expect(names("server")).toEqual([
      ["this-server", ["general", "variables", "secret-managers", "console", "integrations", "about"]],
      ["monitoring", ["notifications", "alerts", "probes", "log-shipping"]],
      ["developers", ["tokens", "api", "manifest", "mcp"]],
    ]);
    expect(names("instance")).toEqual([
      ["people", ["accounts", "access", "sso", "approvals"]],
      ["governance", ["audit", "controls", "compliance"]],
      ["fleet", ["servers", "cloud", "central"]],
    ]);
  });

  it("names the settings sections with the words of the glossary (5.4), the old ones kept as search words", () => {
    const names = Object.fromEntries(DESTS.filter((entry) => entry.parent === "settings").map((entry) => [entry.id, label(entry)]));
    expect(names).toMatchObject({
      "settings.notifications": "Channels",
      "settings.alerts": "Alert rules",
      "settings.probes": "Probes and status page",
      "settings.log-shipping": "Logs and metrics export",
      "settings.console": "Console address",
      "settings.about": "About Noust",
      "settings.access": "Roles",
      "settings.controls": "Controls",
      "settings.compliance": "ENS report",
      "settings.cloud": "Cloud providers",
    });
    expect(text("nav.renamed.notifications")).toBe("Notifications");
    expect(text("nav.renamed.console")).toBe("Console access");
  });

  it("has the account apart from Settings, and no Security among its sections (NV-7)", () => {
    expect(DESTS.map((entry) => entry.id)).not.toContain("settings.security");
    expect(DESTS.filter((entry) => entry.parent === "account").map((entry) => entry.to)).toEqual(["/account/security"]);
    expect(label(dest("account"))).toBe("Account");
    expect(crumbsOf("account.security").map(label)).toEqual(["Account"]);
  });

  it("agrees with nodeRoute.ts about what is the central's", () => {
    const centralPath = (path: string): boolean => CENTRAL_PATHS.some((prefix) => path === prefix || path.startsWith(`${prefix}/`));
    for (const entry of DESTS.filter((candidate) => candidate.central === true)) expect(centralPath(entry.to), `${entry.to} is the central's but nodeRoute.ts lets a node hold it`).toBe(true);
    // An address that only redirects (Security) is the central's too, so that a node's copy of it is dropped, and has no entry.
    for (const prefix of CENTRAL_PATHS.filter((path) => (path.startsWith("/settings/") || path === "/account") && !LIVE_SOURCES.has(path))) {
      expect(DESTS.find((entry) => entry.to === prefix)?.central, `${prefix} is the central's in nodeRoute.ts but not in the register`).toBe(true);
    }
  });

  it("agrees with the subsections of an application's settings", () => {
    const registered = DESTS.filter((entry) => entry.parent === "app.settings").map((entry): [string, string] => [entry.to.replace("/apps/$domain/settings", ""), entry.label]);
    const drawn = SETTINGS_SECTIONS.filter((section) => section.path !== "").map((section): [string, string] => [section.path, section.label]);
    expect(new Map(registered)).toEqual(new Map(drawn));
  });
});

describe("what the pages draw from the register", () => {
  it("draws the tabs the pages drew", () => {
    expect(paths(APP_TABS)).toEqual([
      "/apps/$domain",
      "/apps/$domain/deployments",
      "/apps/$domain/logs",
      "/apps/$domain/metrics",
      "/apps/$domain/environment",
      "/apps/$domain/database",
      "/apps/$domain/domains",
      "/apps/$domain/settings",
    ]);
    expect(APP_TABS[0]).toEqual({ label: "nav.appTabs.overview.label", to: "/apps/$domain", exact: true });
    expect(paths(TEAM_TABS)).toEqual(["/teams/$team", "/teams/$team/members", "/teams/$team/variables", "/teams/$team/notifications", "/teams/$team/settings"]);
    expect(paths(tabsOf(FLEET_APPS_SCREENS))).toEqual(["/fleet/apps", "/fleet/placements"]);
    expect(paths(tabsById("server"))).toEqual(["/server", "/server/updates", "/server/security", "/server/storage", "/server/services", "/server/system"]);
    expect(paths(tabsById("backups"))).toEqual(["/backups", "/backups/schedules", "/backups/volumes", "/backups/noust", "/backups/destinations"]);
    expect(paths(tabsById("domains"))).toEqual(["/domains", "/domains/sites", "/domains/dns", "/domains/tunnels", "/domains/ports"]);
    expect(paths(tabsById("apps"))).toEqual(["/apps", "/catalog", "/catalog/installed"]);
    expect(paths(tabsById("databases"))).toEqual(["/databases", "/databases/engines"]);
    expect(paths(tabsById("service"))).toEqual(["/server/services/$name", "/server/services/$name/logs"]);
  });

  it("keeps the exact tabs that must not stay lit beneath them", () => {
    const exact = (tabs: readonly { to: string; exact?: boolean }[]) => tabs.filter((tab) => tab.exact === true).map((tab) => tab.to);
    expect(exact(tabsById("domains"))).toEqual(["/domains", "/domains/sites", "/domains/dns", "/domains/tunnels", "/domains/ports"]);
    expect(exact(tabsById("backups"))).toEqual(["/backups"]);
    expect(exact(tabsById("database"))).toEqual(["/databases/$engine/$name"]);
  });

  it("draws a strip from the group of screens a page imports exactly as the register says it", () => {
    // A page names the group it draws so its route can be given the texts (nav.ts, head); the group
    // and the entries' parents are two ways to say one thing, and must not drift apart.
    const same = (strip: Strip, id: string): void => {
      expect(tabsOf(strip, EVERYTHING), id).toEqual(tabsById(id, EVERYTHING));
    };
    same(OVERVIEW_SCREENS, "overview");
    same(PROJECTS_SCREENS, "projects");
    same(APPS_SCREENS, "apps");
    same(DATABASES_SCREENS, "databases");
    same(DOMAINS_SCREENS, "domains");
    same(BACKUPS_SCREENS, "backups");
    same(SERVER_SCREENS, "server");
    same(SERVICE_SCREENS, "service");
    same(DATABASE_SCREENS, "database");
    expect(APP_TABS).toEqual(tabsById("app"));
    expect(TEAM_TABS).toEqual(tabsById("team"));
    // The fleet's views are the sidebar's; its pages draw only the strip of the applications' two.
    same(FLEET_APPS_SCREENS, "fleet.apps");
    expect(tabsOf(OVERVIEW_SCREENS).map((tab) => tab.to)).toEqual(["/", "/map"]);
    expect(tabsOf(PROJECTS_SCREENS).map((tab) => tab.to)).toEqual(["/projects", "/projects/teams"]);
  });

  it("draws the tabs of a database from what its engine can do", () => {
    const paths = (capabilities: readonly string[]) => tabsById("database", { capabilities }).map((tab) => tab.to.replace("/databases/$engine/$name", "") || "/");
    expect(paths(["tables", "sql", "dump", "users", "metrics"])).toEqual(["/", "/data", "/query", "/backups", "/users", "/connect", "/metrics"]);
    // A key-value store has Keys where a SQL database has Data, and no query console.
    expect(paths(["keys", "dump"])).toEqual(["/", "/data", "/backups", "/connect"]);
    expect(tabsById("database", { capabilities: ["keys"] }).map((tab) => tab.label)).toContain("databases.detailTabs.keys");
  });

  it("puts a count on the tab it belongs to and leaves the others as they were", () => {
    const tabs = withCounts(tabsById("backups"), { "/backups": 3, "/backups/schedules": null });
    expect(tabs.map((tab) => tab.count)).toEqual([3, null, undefined, undefined, undefined]);
    expect(Object.hasOwn(tabs[2] ?? {}, "count"), "no count means no key, not an undefined one").toBe(false);
  });

  it("walks from a screen up to the list it belongs to", () => {
    expect(crumbsOf("app.settings.source").map((entry) => entry.id)).toEqual(["apps", "app", "app.settings"]);
    expect(crumbsOf("team.members").map((entry) => entry.id)).toEqual(["projects", "projects.teams", "team"]);
    expect(crumbsOf("overview")).toEqual([]);
  });
});

/** Whether the entry is a route and no page yet. */
function isInterim(entry: Dest): boolean {
  return INTERIM.some((row) => row.from === entry.to);
}
