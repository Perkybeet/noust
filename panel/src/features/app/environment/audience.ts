/**
 * Which part of a deployment each variable is given to, and the changes to it an operator has
 * staged but not saved.
 *
 * A variable is for the build and the running application (`both`, the default), for the build
 * alone (it is in no `.env`, only the install and the build see it) or for the running
 * application alone (the build does not). The API names only the variables that are not for
 * both, so a name it does not list is `both`. Changing it is part of the draft the save bar
 * reviews: the file is rewritten whole, so the audience travels with the values.
 */

import { audienceOf } from "../../../api/queries/variables";
import type { VariableAudience } from "../../../api/queries/variables";
import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { translate } from "../../../i18n/translate";

export type { VariableAudience } from "../../../api/queries/variables";

/** Staged audiences by variable name. */
export type StagedAudience = ReadonlyMap<string, VariableAudience>;

export interface AudienceChange {
  name: string;
  before: VariableAudience;
  after: VariableAudience;
}

/** The audience the server records for a name; `both` when it lists none. */
export function storedAudience(recorded: Readonly<Record<string, string>> | undefined, name: string): VariableAudience {
  return audienceOf(recorded?.[name]);
}

/** The audience a row shows: the staged one, else the stored one. */
export function effectiveAudience(recorded: Readonly<Record<string, string>> | undefined, staged: StagedAudience, name: string): VariableAudience {
  return staged.get(name) ?? storedAudience(recorded, name);
}

/**
 * What saving changes about audiences: a staged choice that differs from the stored one, for a
 * variable the file will still hold. A variable that is removed takes its audience with it, and
 * a new one is described by what the dialog chose.
 */
export function audienceChanges(
  recorded: Readonly<Record<string, string>> | undefined,
  staged: StagedAudience,
  names: ReadonlySet<string>,
): AudienceChange[] {
  const changes: AudienceChange[] = [];
  for (const [name, after] of staged) {
    if (!names.has(name)) continue;
    const before = storedAudience(recorded, name);
    if (before !== after) changes.push({ name, before, after });
  }
  return changes;
}

/** The body's `audience` for the changes: only the names that change, as the API takes it. */
export function audienceBody(changes: readonly AudienceChange[]): Record<string, VariableAudience> {
  return Object.fromEntries(changes.map((change) => [change.name, change.after]));
}

/** "Build and runtime", "Build only", "Runtime only". */
export function audienceLabel(audience: VariableAudience, locale: Locale = getLocale()): string {
  return translate(locale, `variables.audience.${audience}`);
}

/** One sentence on what the audience means, for the dialog's help and a menu's description. */
export function audienceHelp(audience: VariableAudience, locale: Locale = getLocale()): string {
  return translate(locale, `variables.audience.${audience}Help`);
}
