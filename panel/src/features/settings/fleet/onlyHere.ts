/**
 * What a server keeps for itself and the fleet does not know: the names a server's own list of a kind holds that the
 * central has no setting of. The central reads them through the server's own typed routes (the same ones it writes a
 * setting by, `operations` of each kind in `fleet/settings/kinds.py`), one server at a time; nothing about it is made
 * a setting of the fleet by being read.
 */

/** The kinds that have a list of names on a server. The policy is one thing a server always has, so it has no "only here". */
export const KINDS_WITH_NAMES = ["channel", "alert_rule", "backup_destination", "registry", "variable", "console_link"] as const;

export type NamedKind = (typeof KINDS_WITH_NAMES)[number];

/** The route a server answers a kind's list on. */
export const LIST_PATH: Record<NamedKind, string> = {
  channel: "/api/notifications/channels",
  alert_rule: "/api/alerts/rules",
  backup_destination: "/api/backup-destinations",
  registry: "/api/registries",
  variable: "/api/variables/server",
  console_link: "/api/notifications/console-url",
};

/** What a server holds of one thing the fleet does not know. */
export interface OnlyHere {
  name: string;
  /** A word that says what it is where its name does not: the address of a console link. */
  detail?: string;
}

type Tree = Record<string, unknown>;

function isTree(value: unknown): value is Tree {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function itemsOf(answer: unknown, key: string): Tree[] {
  if (!isTree(answer)) return [];
  const list = answer[key];
  return Array.isArray(list) ? list.filter(isTree) : [];
}

function namesOf(items: readonly Tree[]): OnlyHere[] {
  return items.flatMap((item) => (typeof item["name"] === "string" && item["name"] !== "" ? [{ name: item["name"] }] : []));
}

/** The name of the console link: the fleet has one of it, and so has a server. */
export const CONSOLE_LINK = "console";

/**
 * What a server's answer to a kind's list says it holds.
 *
 * @param kind - The kind.
 * @param answer - The body of the server's answer.
 * @returns What it holds: a channel only when it is set up (the list also names the types it could be), and the
 *   console link only when the server has an address of its own.
 */
export function heldBy(kind: NamedKind, answer: unknown): OnlyHere[] {
  switch (kind) {
    case "channel":
      return namesOf(itemsOf(answer, "items").filter((item) => item["configured"] === true));
    case "alert_rule":
      return namesOf(itemsOf(answer, "items"));
    case "backup_destination":
      return namesOf(itemsOf(answer, "destinations"));
    case "registry":
      return namesOf(itemsOf(answer, "logins"));
    case "variable":
      return namesOf(itemsOf(answer, "variables"));
    case "console_link": {
      const url = isTree(answer) ? answer["url"] : undefined;
      return typeof url === "string" && url.trim() !== "" ? [{ name: CONSOLE_LINK, detail: url }] : [];
    }
  }
}

/**
 * What a server holds that the fleet does not know.
 *
 * @param held - What the server holds of the kind.
 * @param known - The names the central has a setting of in that kind, for the fleet or for one server.
 * @returns The rest, in the order the server gave them.
 */
export function onlyHere(held: readonly OnlyHere[], known: ReadonlySet<string>): OnlyHere[] {
  return held.filter((entry) => !known.has(entry.name));
}

/** Whether a kind is one this reads. */
export function isNamedKind(kind: string): kind is NamedKind {
  return (KINDS_WITH_NAMES as readonly string[]).includes(kind);
}
