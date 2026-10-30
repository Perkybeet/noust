/**
 * What the central may do on a server, apart from any page: the three levels a server can set and
 * the command that sets one. The ceiling is set on the server's own command line and nowhere else
 * (no API raises it, so a compromised central cannot), which is why the console shows the command
 * and reads the answer again rather than offering a button.
 */

import type { PlainKey } from "../../../i18n";

export const LEVELS = ["read", "deploy", "admin"] as const;
export type Level = (typeof LEVELS)[number];

/** The words of each level, as catalog keys: one literal per case so tsc checks them. */
export const LEVEL_WORDS: Readonly<Record<Level, { label: PlainKey; description: PlainKey }>> = {
  // The names are the fleet's (the Servers list says the same words), so one level is called one thing.
  read: { label: "fleet.access.level.read", description: "serverCentral.level.read.description" },
  deploy: { label: "fleet.access.level.deploy", description: "serverCentral.level.deploy.description" },
  admin: { label: "fleet.access.level.admin", description: "serverCentral.level.admin.description" },
};

/** Whether a published level is one this console knows (a newer Noust may say another: it is shown as unknown). */
export function isLevel(value: string): value is Level {
  return (LEVELS as readonly string[]).includes(value);
}

/**
 * The command that sets the ceiling, run on the server itself. Host access only counts with the
 * `admin` level, so a lower one always says `off`: the command says what will be true after it.
 * A test runs these exact strings through the command line.
 */
export function accessCommand(level: Level, hostAccess: boolean): string {
  return `noust fleet access --level ${level} --host-access ${level === "admin" && hostAccess ? "on" : "off"}`;
}
