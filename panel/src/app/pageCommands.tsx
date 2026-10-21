import type { Command } from "./CommandPalette";
import type { PaletteEntry } from "./nav";

/**
 * The screens of the register as palette results: the one place a screen becomes something the
 * palette finds (NV-14). Settings sections are their own group; each result says where it lives,
 * dimmed, when its name alone does not ("Server › System updates"), and its shortcut when it has one.
 */
export function pageCommands(entries: readonly PaletteEntry[], go: (to: string) => () => void): Command[] {
  return entries.map((entry): Command => {
    const Icon = entry.icon;
    const settings = entry.id === "settings" || entry.id.startsWith("settings.");
    return {
      id: `page:${entry.to}`,
      group: settings ? "Settings" : "Pages",
      label: entry.text,
      icon: <Icon />,
      kind: "navigate",
      run: go(entry.to),
      keywords: entry.keywords,
      ...(entry.path !== entry.text ? { detail: entry.path } : {}),
      ...(entry.shortcut !== undefined ? { shortcut: entry.shortcut } : {}),
    };
  });
}
