import { describe, expect, it } from "vitest";

import { WIDGETS } from "../../components/widgets/catalog";
import { toDocument } from "../../components/widgets/model";
import type { Placed } from "../../components/widgets/model";
import { bindT } from "../../i18n";
import { BUILTIN_IDS, defaultLayout, panelsOf, slugOf, startingLayout } from "./panels";
import type { SavedPanel } from "./panels";

const t = bindT("en");

const EVERYTHING = new Set(WIDGETS.flatMap((widget) => widget.needs).concat(["fleet.read", "apps.read"]));
const TEAM = new Set(["self", "apps.read", "apps.deploy", "backups.read", "backups.run", "databases.read"]);
const can = (held: Set<string>) => (permission: string) => held.has(permission);

function placed(type: string, size: Placed["size"] = "small"): Placed {
  return { id: type, type, size, settings: {} };
}

function saved(id: string, layout: Placed[], patch: Partial<SavedPanel> = {}): SavedPanel {
  return { id, name: "My panel", scope: "server", layout: toDocument(layout), dirty: false, ...patch };
}

describe("the panels that come with the console", () => {
  it("are one of a server and one of the fleet, each a layout of widgets the catalog has", () => {
    for (const scope of ["server", "fleet"] as const) {
      const layout = defaultLayout(scope, can(EVERYTHING));
      expect(layout.length, scope).toBeGreaterThanOrEqual(8);
      for (const item of layout) {
        const widget = WIDGETS.find((candidate) => candidate.type === item.type);
        expect(widget, item.type).toBeDefined();
        expect(widget?.sizes, `${item.type} ${item.size}`).toContain(item.size);
        expect(widget?.scopes, item.type).toContain(scope);
      }
      expect(new Set(layout.map((item) => item.id)).size, scope).toBe(layout.length);
    }
  });

  it("begin, for a server, with what needs attention and the six figures, as bands across the page", () => {
    expect(defaultLayout("server", can(EVERYTHING)).slice(0, 2).map((item) => [item.type, item.size])).toEqual([
      ["attention", "wide"],
      ["figures", "wide"],
    ]);
  });

  it("begin, for the fleet, with what needs attention and then the servers", () => {
    expect(defaultLayout("fleet", can(EVERYTHING)).slice(0, 2).map((item) => [item.type, item.size])).toEqual([
      ["attention", "wide"],
      ["fleet", "xlarge"],
    ]);
  });

  it("leave out what a role may not see: a team member's panel is the applications', with no machine in it", () => {
    const layout = defaultLayout("server", can(TEAM));
    expect(layout.map((item) => item.type)).toEqual(expect.arrayContaining(["work", "certificates", "backups", "apps", "deployments"]));
    expect(layout.map((item) => item.type)).not.toEqual(expect.arrayContaining(["resources"]));
    expect(layout.map((item) => item.type)).not.toContain("attention");
    expect(defaultLayout("fleet", can(TEAM))).toEqual([]);
  });
});

describe("the panels a person has", () => {
  it("are the two that come with the console, then their own in the order they were made", () => {
    const list = panelsOf([saved("data", [placed("backups")], { name: "Data" }), saved("edge", [placed("apps")], { name: "Edge", scope: "fleet" })], can(EVERYTHING), t, true);
    expect(list.map((panel) => [panel.id, panel.name, panel.builtin])).toEqual([
      ["server", "This server", true],
      ["fleet", "Fleet", true],
      ["data", "Data", false],
      ["edge", "Edge", false],
    ]);
    expect(list[2]?.layout.map((item) => item.type)).toEqual(["backups"]);
  });

  it("have no panel of the fleet, nor one that follows it, when there is no fleet", () => {
    const list = panelsOf([saved("edge", [placed("apps")], { name: "Edge", scope: "fleet" }), saved("data", [placed("backups")], { name: "Data" })], can(EVERYTHING), t, false);
    expect(list.map((panel) => panel.id)).toEqual(["server", "data"]);
  });

  it("show what the person saved over a panel that comes with the console, and say it was changed", () => {
    const list = panelsOf([saved("server", [placed("apps", "wide")])], can(EVERYTHING), t, true);
    expect(list[0]).toMatchObject({ id: "server", builtin: true, customised: true });
    expect(list[0]?.layout.map((item) => [item.type, item.size])).toEqual([["apps", "wide"]]);
    expect(list[1]?.customised).toBe(false);
  });

  it("keep the console's own name for the ones that come with it, whatever name was stored", () => {
    expect(panelsOf([saved("server", [placed("apps")], { name: "Mi servidor" })], can(EVERYTHING), t, true)[0]?.name).toBe("This server");
  });

  it("are the console's panel again when what was saved is damaged or holds nothing this role may see", () => {
    const damaged = { ...saved("server", []), layout: { version: 9, widgets: [] } } as unknown as SavedPanel;
    expect(panelsOf([damaged], can(EVERYTHING), t, true)[0]?.customised).toBe(false);
    expect(panelsOf([saved("server", [placed("resources")])], can(TEAM), t, true)[0]).toMatchObject({ customised: true, layout: [] });
  });

  it("drop from a panel of their own the widgets their role may not see now", () => {
    const list = panelsOf([saved("mine", [placed("apps"), placed("resources")])], can(TEAM), t, true);
    expect(list.find((panel) => panel.id === "mine")?.layout.map((item) => item.type)).toEqual(["apps"]);
  });
});

describe("the id of a panel", () => {
  it("is its name in lower case letters, digits and hyphens, with no accent", () => {
    expect(slugOf("Datos y copias", [])).toBe("datos-y-copias");
    expect(slugOf("Producción — Madrid", [])).toBe("produccion-madrid");
    expect(slugOf("  --A  B--  ", [])).toBe("a-b");
  });

  it("is never one the server or the console gives meaning to", () => {
    for (const reserved of ["order", "new", ...Object.values(BUILTIN_IDS)]) expect(slugOf(reserved, [])).toBe(`${reserved}-2`);
  });

  it("is never one another panel has", () => {
    expect(slugOf("Data", ["data"])).toBe("data-2");
    expect(slugOf("Data", ["data", "data-2"])).toBe("data-3");
  });

  it("is something even for a name with no letters at all, and no longer than the server takes", () => {
    expect(slugOf("§§§", [])).toBe("panel");
    const long = slugOf("a".repeat(100), []);
    expect(long.length).toBeLessThanOrEqual(40);
    expect(long).toMatch(/^[a-z0-9][a-z0-9-]{0,39}$/);
    expect(slugOf("a".repeat(100), [long]).length).toBeLessThanOrEqual(40);
  });
});

describe("a new panel", () => {
  it("begins as a copy of the console's, or empty", () => {
    expect(startingLayout("server", "copy", can(EVERYTHING)).length).toBeGreaterThan(0);
    expect(startingLayout("server", "empty", can(EVERYTHING))).toEqual([]);
  });
});
