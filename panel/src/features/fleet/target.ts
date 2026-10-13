/**
 * The version a fleet is heading for, and how many of its servers are there (checklist N-30,
 * owner item 81).
 *
 * OBS publishes the packages one distribution at a time, so for a while after a release some
 * servers can already update, others wait for their package, and others run it. The target is
 * the newest version any server's own package source knows about; a server that says it is up
 * to date has nothing newer to offer, so its own version is its answer.
 */

import { compareVersions } from "../../nodes/servers";
import { noustOf, noustUpdatable } from "./data";
import type { FleetRow } from "./data";

export interface FleetTarget {
  /** The newest version any server's source offers or already runs. */
  version: string;
  /** Servers that gave a version. */
  total: number;
  /** Servers already running it (or newer). */
  onTarget: number;
  /** Servers that can update to it now. */
  updatable: number;
  /** Servers whose package is still being published. */
  onTheWay: number;
}

/**
 * The fleet's target version and where each server stands against it.
 *
 * @param rows - The Updates view's rows, one per server.
 * @returns The target, or null when no server reported a version.
 */
export function fleetTarget(rows: readonly FleetRow[]): FleetTarget | null {
  const servers = rows.map(noustOf).filter((noust) => noust.current !== null && noust.state !== "unknown");
  let version: string | null = null;
  for (const noust of servers) {
    const known = noust.state === "up_to_date" ? noust.current : (noust.latest ?? noust.current);
    if (known === null) continue;
    if (version === null || (compareVersions(known, version) ?? 0) > 0) version = known;
  }
  if (version === null) return null;
  const target = version;
  const behind = (current: string | null): boolean => current !== null && (compareVersions(current, target) ?? 0) < 0;
  return {
    version: target,
    total: servers.length,
    onTarget: servers.filter((noust) => !behind(noust.current)).length,
    updatable: servers.filter((noust) => behind(noust.current) && noustUpdatable(noust.state)).length,
    onTheWay: servers.filter((noust) => behind(noust.current) && noust.state === "on_the_way").length,
  };
}
