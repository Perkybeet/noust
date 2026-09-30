/**
 * The three things an account can be to a database - its owner, a reader and writer, a reader
 * - in the console's words. "Custom" is whatever the engine's grants add up to otherwise;
 * Noust shows it but never sets it.
 */

import type { PlainKey } from "../../../i18n";

export type Profile = "owner" | "read_write" | "read_only";

export const PROFILES: readonly Profile[] = ["owner", "read_write", "read_only"];

const LABELS: Readonly<Record<string, PlainKey>> = {
  owner: "databases.profiles.owner",
  read_write: "databases.profiles.readWrite",
  read_only: "databases.profiles.readOnly",
  custom: "databases.profiles.custom",
};

const HELP: Readonly<Record<Profile, PlainKey>> = {
  owner: "databases.profiles.ownerHelp",
  read_write: "databases.profiles.readWriteHelp",
  read_only: "databases.profiles.readOnlyHelp",
};

export function profileLabel(profile: string): PlainKey {
  return LABELS[profile] ?? "databases.profiles.custom";
}

export function profileHelp(profile: Profile): PlainKey {
  return HELP[profile];
}

export function isProfile(value: string): value is Profile {
  return (PROFILES as readonly string[]).includes(value);
}
