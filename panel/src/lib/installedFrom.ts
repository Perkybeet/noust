/**
 * Where a Noust was installed from when that is an app store (`installed_from` of `GET /api/system/version`, `/api/system/update` and the
 * fleet's view of each server): a closed list the server says, of which today there is one, the NAS's App Center. A Noust installed
 * from a store is updated by the store, so wherever the console says how Noust is updated it says that instead, and shows neither a
 * command nor a button that would update it from here (item 198). Nothing here is worked out from the machine: the server says it.
 */

import type { T } from "../i18n";

/** The app store the server says Noust was installed from; null for an installation the console updates itself, or a server that says nothing. */
export type InstalledFrom = string | null | undefined;

/** Whether the installation is updated by an app store, and not by anything the console could run. */
export function updatedByStore(installedFrom: InstalledFrom): boolean {
  return typeof installedFrom === "string" && installedFrom !== "";
}

/**
 * What to say where the console says how Noust is updated, for an installation that an app store updates: that the new versions arrive
 * through the store, in the store's own name when the console knows it, and in general words when a newer server names another.
 *
 * @param t - The translator.
 * @param installedFrom - What the server says Noust was installed from.
 * @returns The sentence, or null when the installation is not one a store updates.
 */
export function storeUpdateWords(t: T, installedFrom: InstalledFrom): string | null {
  if (!updatedByStore(installedFrom)) return null;
  return installedFrom === "ugreen-app-center" ? t("settings.about.version.viaStore") : t("settings.about.version.viaStoreOther");
}
