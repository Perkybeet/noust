import { describe, expect, it } from "vitest";

import * as en from "../i18n/en";
import type { PlainKey } from "../i18n";
import { filterCommands } from "./CommandPalette";
import type { Command } from "./CommandPalette";
import { DESTS, RENAMED, paletteEntries } from "./nav";
import { pageCommands } from "./pageCommands";

/**
 * The palette finds every screen of the register (docs/DESIGN.md, NV-14): by its own name first, by
 * every word it is known by, by the names it had before, and by the place it lives in; and what a
 * role may not see is not offered. The commands are built the way the palette builds them
 * (`pageCommands`), over the same entries, so a screen that is in the register cannot be missing from
 * the palette, and one that is not in it cannot be found.
 */

function text(key: string): string {
  let node: unknown = en;
  for (const part of key.split(".")) node = (node as Record<string, unknown> | undefined)?.[part];
  if (typeof node !== "string") throw new Error(`the catalog has no text for "${key}"`);
  return node;
}

function commandsFor(over: { can?: (permission: string) => boolean } = {}): { commands: Command[]; ids: Map<string, string> } {
  const entries = paletteEntries({ t: (key: PlainKey) => text(key), can: () => true, when: { organised: true, fleet: true }, ...over });
  const commands = pageCommands(entries, () => () => undefined);
  return { commands, ids: new Map(entries.map((entry) => [entry.id, `page:${entry.to}`])) };
}

const found = (commands: readonly Command[], query: string): string[] => filterCommands(commands, query).flatMap((group) => group.items.map((item) => item.id));

describe("the palette over the register", () => {
  const { commands, ids } = commandsFor();
  const entries = paletteEntries({ t: (key: PlainKey) => text(key), can: () => true, when: { organised: true, fleet: true } });

  it("offers a screen for every entry of the register that a path of no parameters names", () => {
    const expected = DESTS.filter((entry) => !entry.to.includes("$")).map((entry) => entry.id);
    expect(entries.map((entry) => entry.id)).toEqual(expected);
    expect(commands).toHaveLength(entries.length);
  });

  it("finds each one first by its own name", () => {
    for (const entry of entries) {
      expect(found(commands, entry.text)[0], `"${entry.text}"`).toBe(ids.get(entry.id));
    }
  });

  it("finds each one by every word it is known by, and by where it lives", () => {
    for (const entry of entries) {
      const id = ids.get(entry.id);
      for (const word of entry.keywords.split(/\s+/).filter((candidate) => candidate.length > 1)) {
        expect(found(commands, word), `"${word}" does not find ${entry.id}`).toContain(id);
      }
      expect(found(commands, entry.path), `"${entry.path}" does not find ${entry.id}`).toContain(id);
    }
  });

  it("finds a renamed screen by the name it had", () => {
    for (const [id, keys] of Object.entries(RENAMED)) {
      for (const key of keys) expect(found(commands, text(key)), `"${text(key)}" does not find ${id}`).toContain(ids.get(id));
    }
  });

  it("finds what the operator named in the task list: the terminal, the schedules, DNS, the metrics export", () => {
    const first = (query: string): string => {
      const id = found(commands, query)[0];
      return entries.find((entry) => `page:${entry.to}` === id)?.id ?? `nothing for "${query}"`;
    };
    expect(first("terminal")).toBe("server.terminal");
    expect(first("schedules")).toBe("backups.schedules");
    expect(first("dns")).toBe("domains.dns");
    expect(found(commands, "prometheus")).toContain(ids.get("settings.log-shipping"));
    expect(found(commands, "slack")).toContain(ids.get("settings.notifications"));
  });

  it("says where each result lives, to the right, when its name alone does not", () => {
    // A screen with a phrase of its own says its place beside it; one named by its place does not repeat it.
    const alerts = commands.find((command) => command.id === "page:/settings/alerts");
    expect(alerts).toMatchObject({ label: "Alert rules settings", detail: "Settings › Alert rules" });
    const updates = commands.find((command) => command.id === "page:/server/updates");
    expect(updates).toMatchObject({ label: "Server › System updates" });
    expect(updates?.detail).toBeUndefined();
    const apps = commands.find((command) => command.id === "page:/apps");
    expect(apps?.detail).toBeUndefined();
    expect(apps?.shortcut).toEqual(["g", "a"]);
  });

  it("leaves out what a role cannot see, in the palette too", () => {
    const { commands: viewer } = commandsFor({ can: () => false });
    const offered = new Set(viewer.map((command) => command.id));
    for (const entry of DESTS.filter((candidate) => candidate.permission !== undefined && !candidate.to.includes("$"))) {
      expect(offered.has(`page:${entry.to}`), `${entry.id} needs ${entry.permission ?? ""}`).toBe(false);
    }
    expect(offered.has("page:/apps")).toBe(true);
  });
});
