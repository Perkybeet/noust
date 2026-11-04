import { describe, expect, it } from "vitest";

import { bindT, hasMessage, loadCatalog } from "../../i18n";
import { CATEGORY_ORDER, WIDGETS, WIDGET_TYPES, faceFor, isOffered, newPlaced, offeredWidgets, sizesOf, widgetOf } from "./catalog";
import { SIZE_ORDER } from "./model";
import type { Placed } from "./model";

/** What an instance administrator holds, as far as Home is concerned. */
const EVERYTHING = new Set(["self", "apps.read", "backups.read", "databases.read", "server.read", "fleet.read", "apps.deploy", "apps.manage", "backups.run", "databases.write", "server.manage", "server.terminal", "fleet.manage", "accounts.manage"]);
/** What a role of a team holds: the applications, the databases and the backups, and nothing of the machine. */
const TEAM = new Set(["self", "apps.read", "apps.operate", "apps.deploy", "databases.read", "databases.write", "backups.read", "backups.run", "teams.read"]);
/** What a person who may only look at the fleet holds. */
const FLEET_VIEWER = new Set(["self", "fleet.read", "apps.read", "server.read"]);

const can = (held: Set<string>) => (permission: string) => held.has(permission);

describe("the catalog", () => {
  it("has a definition for each widget the stored panels may name, once", () => {
    expect(WIDGETS.map((widget) => widget.type).sort()).toEqual([...WIDGET_TYPES].sort());
    expect(new Set(WIDGETS.map((widget) => widget.type)).size).toBe(WIDGETS.length);
  });

  it("gives each a size it can be added at, known sizes only, and a place to be", () => {
    for (const widget of WIDGETS) {
      expect(widget.sizes.length, widget.type).toBeGreaterThan(0);
      for (const size of widget.sizes) expect(SIZE_ORDER, `${widget.type} ${size}`).toContain(size);
      expect(widget.scopes.length, widget.type).toBeGreaterThan(0);
      expect(CATEGORY_ORDER, widget.type).toContain(widget.category);
    }
  });

  it("names every widget in both languages: a title and one line for the gallery", async () => {
    await loadCatalog("es");
    for (const widget of WIDGETS) {
      for (const field of ["title", "line"] as const) {
        const key = `widgets.${widget.type}.${field}` as const;
        expect(hasMessage(key), key).toBe(true);
        expect(bindT("es")(key), key).not.toBe(bindT("en")(key));
      }
    }
  });

  it("makes the id of a widget that is free, and puts it at its first size", () => {
    const apps = widgetOf("apps");
    if (apps === undefined) throw new Error("no apps widget");
    const layout: Placed[] = [{ id: "apps", type: "apps", size: "small", settings: {} }];
    expect(newPlaced(apps, [])).toMatchObject({ id: "apps", type: "apps", size: apps.sizes[0] });
    expect(newPlaced(apps, layout).id).toBe("apps-2");
  });

  it("knows no widget of a type it has not heard of", () => {
    expect(widgetOf("from-the-future")).toBeUndefined();
    expect(faceFor(bindT("en"), "server")({ id: "x", type: "from-the-future", size: "small", settings: {} })).toBeNull();
  });
});

describe("what a role is offered", () => {
  it("is everything for the administrator of a server, and the fleet's only on a fleet panel", () => {
    const server = offeredWidgets("server", can(EVERYTHING)).map((widget) => widget.type);
    expect(server).toContain("resources");
    expect(server).toContain("attention");
    expect(server).not.toContain("fleet");
    const fleet = offeredWidgets("fleet", can(EVERYTHING)).map((widget) => widget.type);
    expect(fleet).toContain("fleet");
    expect(fleet).toContain("attention");
    expect(fleet).not.toContain("resources");
  });

  it("is none of the machine's for a role of a team", () => {
    const offered = offeredWidgets("server", can(TEAM));
    for (const widget of offered) expect(widget.needs, widget.type).not.toContain("server.read");
    expect(offered.map((widget) => widget.type)).toEqual(expect.arrayContaining(["apps", "work", "deployments", "backups", "certificates", "databases", "tasks", "actions"]));
    expect(offered.map((widget) => widget.type)).not.toEqual(expect.arrayContaining(["resources"]));
  });

  it("is nothing for a fleet panel to someone who may not read the fleet", () => {
    expect(offeredWidgets("fleet", can(TEAM))).toEqual([]);
    expect(offeredWidgets("fleet", can(FLEET_VIEWER)).length).toBeGreaterThan(0);
  });

  it("holds each widget to what it needs, all of it", () => {
    const resources = widgetOf("resources");
    if (resources === undefined) throw new Error("no resources widget");
    expect(isOffered(resources, "server", can(new Set(["apps.read"])))).toBe(false);
    expect(isOffered(resources, "server", can(new Set(["server.read"])))).toBe(true);
    expect(isOffered(resources, "fleet", can(EVERYTHING))).toBe(false);
  });

  it("is what a stored panel is read against: a widget the role may not see is dropped", () => {
    const known = sizesOf("server", can(TEAM));
    expect(known("apps")).not.toBeNull();
    expect(known("resources")).toBeNull();
    expect(known("from-the-future")).toBeNull();
  });

  it("is a gallery that gives a widget of the machine to nobody who may not read the machine", () => {
    for (const widget of offeredWidgets("server", can(TEAM))) expect(widget.category === "fleet", widget.type).toBe(false);
  });
});
