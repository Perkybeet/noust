/** The System tab's lower view, as it lives in the URL (`?view=network`). */

export const SYSTEM_VIEWS = ["processes", "network", "monitor"] as const;
export type SystemView = (typeof SYSTEM_VIEWS)[number];

export interface SystemSearch {
  view?: Exclude<SystemView, "processes">;
}

/** Reads `?view=`, dropping anything that is not a view (the default, processes, is no parameter). */
export function validateSystemSearch(search: Record<string, unknown>): SystemSearch {
  const view = search["view"];
  return typeof view === "string" && view !== "processes" && (SYSTEM_VIEWS as readonly string[]).includes(view)
    ? { view: view as Exclude<SystemView, "processes"> }
    : {};
}

/** Every time zone the browser knows, for the time zone field's suggestions. */
export function knownTimeZones(): readonly string[] {
  const intl = Intl as unknown as { supportedValuesOf?: (key: string) => string[] };
  try {
    return intl.supportedValuesOf?.("timeZone") ?? [];
  } catch {
    // An engine without the list: the field still takes any name, the server checks it.
    return [];
  }
}
