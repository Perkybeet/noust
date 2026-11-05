/**
 * The firewall's rules, read the way the page says them: what each is for, who made it and whether Noust keeps its
 * port open. Pure, so each reading is tested without drawing a table.
 */

import type { Holder, PortRow } from "../server/network/data";
import type { Firewall, FirewallRule } from "../server/queries";

/** The ports of a rule as the firewall writes them: `80, 443`, a range `8000:8100`, or `*` for every port. */
export function rulePorts(rule: FirewallRule): string {
  if (rule.ports.length === 0) return "*";
  return rule.ports.map(([from, to]) => (from === to ? String(from) : `${String(from)}:${String(to)}`)).join(", ");
}

export type RuleSubject = { kind: "service"; name: string } | { kind: "holder"; holder: Holder } | { kind: "none" };

/**
 * What a rule is for: the service the firewall itself names (an application profile of ufw), else what holds its port in
 * Noust (an application, a database, a published port), else nothing is known.
 */
export function ruleSubject(rule: FirewallRule, rows: readonly PortRow[]): RuleSubject {
  if (rule.service !== null && rule.service !== undefined && rule.service !== "") return { kind: "service", name: rule.service };
  for (const [from = 0, to = 0] of rule.ports) {
    const row = rows.find((entry) => entry.port >= from && entry.port <= to);
    const holder = row?.holders[0];
    if (holder !== undefined) return { kind: "holder", holder };
  }
  return { kind: "none" };
}

export type RuleMaker =
  | { kind: "stream"; name: string }
  | { kind: "database"; engine: string; name: string }
  | { kind: "dns" }
  | { kind: "noust"; note: string }
  | { kind: "other"; note: string };

/**
 * Who made a rule, from the note Noust writes on its own (`noust: db postgresql tienda_prod 5432`). A rule Noust did not
 * make is somebody else's, and is shown with their note, if they left one.
 */
export function ruleMaker(rule: FirewallRule): RuleMaker {
  const comment = rule.comment.trim();
  if (!rule.noust) return { kind: "other", note: comment };
  const note = comment.replace(/^noust:\s*/, "");
  const stream = /^noust stream (\S+)$/.exec(note);
  if (stream?.[1] !== undefined) return { kind: "stream", name: stream[1] };
  const database = /^db (\S+) (\S+) \d+$/.exec(note);
  if (database?.[1] !== undefined && database[2] !== undefined) return { kind: "database", engine: database[1], name: database[2] };
  if (note === "noust dns") return { kind: "dns" };
  return { kind: "noust", note };
}

/** The port the anti-lockout guard keeps open that this rule carries, with its reason; null for any other rule. */
export function isProtected(rule: FirewallRule, guard: Firewall["protected_ports"]): Firewall["protected_ports"][number] | null {
  return guard.find((entry) => rule.ports.some(([from = 0, to = 0]) => entry.port >= from && entry.port <= to)) ?? null;
}
