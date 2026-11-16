import { describe, expect, it } from "vitest";

import type { App } from "../api/queries/apps";
import { bindT } from "../i18n";
import { appSectionCommands } from "./appCommands";
import { filterCommands } from "./CommandPalette";

const go = () => () => undefined;

function idsFor(query: string, over: { app?: App; can?: (permission: string) => boolean } = {}): string[] {
  const commands = appSectionCommands("shop.example.com", over.app, bindT("en"), go, over.can === undefined ? {} : { can: over.can });
  return filterCommands(commands, query).flatMap((group) => group.items.map((item) => item.id));
}

describe("what the palette finds inside an application", () => {
  it("offers the sections of its strip, named with the application", () => {
    const commands = appSectionCommands("shop.example.com", undefined, bindT("en"), go);
    expect(commands.find((command) => command.id === "app-section:/apps/$domain/deployments")?.label).toBe("Deployments: shop.example.com");
  });

  it("offers the sections the column groups under Fix as well, so the shell is found from the application (item 160)", () => {
    // Terminal, Diagnose and Files are not tabs, but they are sections of the application: "migrate" is run in its terminal.
    for (const query of ["migrate", "manage.py", "run once", "one-off"]) {
      expect(idsFor(query), `"${query}"`).toContain("app-section:/apps/$domain/terminal");
    }
    expect(idsFor("terminal")).toContain("app-section:/apps/$domain/terminal");
    expect(idsFor("why down")).toContain("app-section:/apps/$domain/diagnose");
    expect(idsFor("upload")).toEqual(expect.arrayContaining(["app-section:/apps/$domain/files", "app-section:/apps/$domain/domains"]));
  });

  it("leaves out what the principal may not open: the shell of an application needs its own permission", () => {
    const readOnly = (permission: string): boolean => !permission.startsWith("apps.terminal");
    expect(idsFor("terminal", { can: readOnly })).not.toContain("app-section:/apps/$domain/terminal");
    expect(idsFor("terminal", { can: () => true })).toContain("app-section:/apps/$domain/terminal");
  });

  it("does not offer twice what the strip already lists", () => {
    const commands = appSectionCommands("shop.example.com", undefined, bindT("en"), go);
    const unique = new Set(commands.map((command) => command.id));
    expect(unique.size).toBe(commands.length);
  });
});
