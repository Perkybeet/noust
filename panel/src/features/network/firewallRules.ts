/**
 * The firewall's rules, read the way the page says them: what each is for, who made it and whether Noust keeps its
 * port open. Pure, so each reading is tested without drawing a table.
 */

import { parseAddresses } from "../../lib/addresses";
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
 * Noust (an application, a database, a published port), else the program the kernel says listens there, else nothing
 * is known.
 */
export function ruleSubject(rule: FirewallRule, rows: readonly PortRow[]): RuleSubject {
  if (rule.service !== null && rule.service !== undefined && rule.service !== "") return { kind: "service", name: rule.service };
  for (const [from = 0, to = 0] of rule.ports) {
    const row = rows.find((entry) => entry.port >= from && entry.port <= to);
    const holder = row?.holders[0];
    if (holder !== undefined) return { kind: "holder", holder };
    const program = row?.firewall?.process;
    if (program !== undefined && program !== null && program !== "") return { kind: "service", name: program };
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

/** Where the rule should admit from: anyone, or one address or network. */
export type SourceChoice = "any" | "some";

/** Why an origin cannot be asked for, or null when it can: nothing typed, not an address, more than one, or the one it has. */
export type SourceProblem = "needed" | "invalid" | "one" | "same";

/**
 * Reads the origin a person typed for a rule. A rule has one origin (an address or a network), so a list is "one";
 * what is already the rule's own is "same", whatever the spelling of a host (`10.0.0.5` is `10.0.0.5/32`).
 */
export function sourceProblem(choice: SourceChoice, text: string, current: string): SourceProblem | null {
  const everyone = current === "any" || current === "";
  if (choice === "any") return everyone ? "same" : null;
  if (text.trim() === "") return "needed";
  const { entries, invalid } = parseAddresses(text);
  if (invalid.length > 0) return "invalid";
  if (entries.length !== 1) return "one";
  const [entry = ""] = entries;
  return !everyone && sameOrigin(entry, current) ? "same" : null;
}

/** An address with no length is a host: `10.0.0.5` and `10.0.0.5/32` admit the same. */
function sameOrigin(first: string, second: string): boolean {
  const whole = (value: string): string => (value.includes("/") ? value : `${value}/${value.includes(":") ? "128" : "32"}`);
  return whole(first).toLowerCase() === whole(second).toLowerCase();
}

/** The `source` of `POST .../firewall/rules/{rule_id}/source`: `any`, or the one address or network typed. */
export function sourceBody(choice: SourceChoice, text: string): string {
  return choice === "any" ? "any" : (parseAddresses(text).entries[0] ?? "");
}
