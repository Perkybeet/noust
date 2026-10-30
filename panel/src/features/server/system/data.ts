/**
 * The views of the System and updates section, as they live in the URL (`?view=machine`): what is
 * waiting to be updated first (the task most people come for), then the machine itself, what runs
 * on it and what it uses. The network interfaces are Network and ports'.
 */

export const SYSTEM_VIEWS = ["updates", "machine", "processes", "monitor"] as const;
export type SystemView = (typeof SYSTEM_VIEWS)[number];

export interface SystemSearch {
  /** The default, updates, is no parameter; `network` is the 4.0 address of the interfaces, which the route sends on. */
  view?: Exclude<SystemView, "updates"> | "network";
}

/** Reads `?view=`, dropping anything that is not a view of this section or the one 4.0 address that moved. */
export function validateSystemSearch(search: Record<string, unknown>): SystemSearch {
  const view = search["view"];
  if (view === "network") return { view: "network" };
  return typeof view === "string" && view !== "updates" && (SYSTEM_VIEWS as readonly string[]).includes(view) ? { view: view as Exclude<SystemView, "updates"> } : {};
}
