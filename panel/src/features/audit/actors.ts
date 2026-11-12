/**
 * Who acted in the audit log, in words. Its own module and not `data.ts`: the audit route reads its filters from
 * `data.ts` with the first page (`validateSearch`), and what that imports is in the first load, which Activity's words
 * and the roles are not.
 */

import type { AuditEntry } from "../../api/queries/audit";
import type { T } from "../../i18n";
import { describeActor, describeWho } from "../activity/data";
import type { ActorWords } from "../activity/data";
import { roleLabel } from "../settings/accounts/roles";

/**
 * Who acted, in the words Activity uses for the same credential (item 152): the master token is "The master token"
 * here as there, a person is their name, a token is `Token "ci"`. `raw` is what the server recorded.
 */
export function actorWordsOf(t: T, entry: Pick<AuditEntry, "actor" | "who">): ActorWords {
  return entry.who !== null && entry.who !== undefined ? describeWho(t, entry.who, entry.actor) : describeActor(t, entry.actor);
}

/**
 * The role line under the actor. The events of the master token carry the way it holds the console (`break_glass`,
 * `recovery`, `compat`) where an account carries its role: said as what it means, never as the word the server wrote.
 */
export function actorRoleWords(t: T, entry: Pick<AuditEntry, "who">): string | null {
  const role = entry.who?.role;
  if (role === undefined || role === null || role === "") return null;
  if (entry.who?.kind === "master" && (role === "compat" || role === "break_glass" || role === "recovery")) return t(`audit.grant.${role}`);
  return roleLabel(t, role);
}
