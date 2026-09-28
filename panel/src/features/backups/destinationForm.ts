/**
 * The pure rules behind `DestinationDialog`'s form: which required fields still need a value,
 * and what `fields` payload a create or update request sends. Kept apart from the component so
 * they are tested without rendering anything.
 */

import type { BackendField } from "../../api/queries/backupDestinations";

/**
 * Whether every required field has a value. A required secret left blank still counts when it
 * is already stored (editing a destination: a blank secret field keeps what is saved).
 */
export function hasRequiredValues(
  fields: readonly BackendField[],
  values: Readonly<Record<string, string>>,
  configuredSecrets: ReadonlySet<string> = new Set(),
): boolean {
  return fields.every((field) => {
    if (!field.required) return true;
    if ((values[field.key] ?? "").trim() !== "") return true;
    return field.secret && configuredSecrets.has(field.key);
  });
}

/**
 * The `fields` map a create or update request sends: every non-secret field as typed, and a
 * secret field only when something was typed for it - blank keeps the stored value on an
 * update, and the API rejects a create missing a required one.
 */
export function fieldsPayload(fields: readonly BackendField[], values: Readonly<Record<string, string>>): Record<string, string> {
  const payload: Record<string, string> = {};
  for (const field of fields) {
    const value = (values[field.key] ?? "").trim();
    if (field.secret && value === "") continue;
    payload[field.key] = value;
  }
  return payload;
}
