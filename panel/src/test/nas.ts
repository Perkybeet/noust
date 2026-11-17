/**
 * The sessions of the two machines a central of a NAS can be, for the tests of what the console offers on each: a hub that deploys
 * containers on its machine's Docker, and a hub that was not told to. What the table says is what the server publishes for each
 * (`noust.central.features`: read from the console server's model of a NAS), written as one boolean for each function so that a
 * function the table gains is a compile error here and the test that ties the page's words to it fails until it is read.
 */

import type { SessionInfo } from "../api/queries/auth";
import type { components } from "../api/schema.gen";
import { SESSION } from "./fakes";

type FeatureStates = components["schemas"]["FeatureStates"];
export type TableKey = keyof FeatureStates;

/** Whether each function of the table works on a hub that deploys containers on its machine's Docker: Docker is all they need, or they are refused. */
export const NAS_ON: Readonly<Record<TableKey, boolean>> = {
  application_backups: true,
  application_diagnosis: false,
  application_environments: true,
  applications: true,
  certificates: false,
  checking_a_moved_server: false,
  console_exposure: false,
  container_deploy: true,
  database_backups: false,
  database_instances: true,
  databases: false,
  deploy_here: true,
  dns_accounts: true,
  dns_records: true,
  docker_zone: true,
  domains: false,
  firewall: false,
  importing_applications: false,
  log_destinations: false,
  moving_a_server: false,
  native_deploy: false,
  noust_dns: false,
  point_in_time_recovery: false,
  power: false,
  preview_environments: false,
  process_monitor: false,
  public_database_access: false,
  published_ports: false,
  recipes: true,
  releases: true,
  restoring_a_server: false,
  rollbacks: true,
  root_shell: false,
  scheduled_jobs: false,
  server_health: false,
  server_security: false,
  server_setup: false,
  services: false,
  sites: false,
  software: false,
  status_page: false,
  system_clock: false,
  system_files: false,
  system_logs: false,
  system_processes: false,
  system_updates: false,
  tunnels: true,
  volume_backups: true,
  zero_downtime: false,
};

/** What the machine decides and no role does: a container has no systemd, no journal, no root and, here, no Docker of its own to show. */
const MACHINE_ONLY: ReadonlyMap<TableKey, string> = new Map<TableKey, string>([
  ["firewall", "features.needs_systemd"],
  ["power", "features.needs_systemd"],
  ["root_shell", "features.needs_root"],
  ["server_security", "features.needs_systemd"],
  ["system_clock", "features.needs_systemd"],
  ["system_logs", "features.needs_journal"],
  ["docker_zone", "docker.engine.missing"],
  // What the pages of the machine show is read from the machine Noust runs on: in the central's image that is the container.
  ["system_processes", "features.needs_host"],
  ["system_files", "features.needs_host"],
  ["system_updates", "features.needs_host"],
]);

function reason(key: TableKey, code: string) {
  return { code, params: { feature: key }, text: `${key}: not available here`, hint: null };
}

/** The table of a hub that deploys containers: what needs only Docker works, and the rest is refused for deploying containers only. */
function tableOn(): NonNullable<NonNullable<SessionInfo["central"]>["features"]> {
  const states: Record<string, unknown> = {};
  for (const key of Object.keys(NAS_ON) as TableKey[]) {
    states[key] = { available: NAS_ON[key], reason: NAS_ON[key] ? null : reason(key, MACHINE_ONLY.get(key) ?? "features.containers_only") };
  }
  return states as never;
}

/** The table of a hub that was not told to: nothing works, for being a hub, or for what the machine lacks, but the page that turns Docker on, which is a hub's. */
function tableOff(): NonNullable<NonNullable<SessionInfo["central"]>["features"]> {
  const states: Record<string, unknown> = {};
  for (const key of Object.keys(NAS_ON) as TableKey[]) {
    states[key] = key === "deploy_here" ? { available: true, reason: null } : { available: false, reason: reason(key, MACHINE_ONLY.get(key) ?? "features.hub_role") };
  }
  return states as never;
}

/** The table of a server: it deploys on its own machine without being asked, so the page that turns Docker on is not its. */
export function serverTable(): NonNullable<NonNullable<SessionInfo["central"]>["features"]> {
  const states: Record<string, unknown> = {};
  for (const key of Object.keys(NAS_ON) as TableKey[]) {
    states[key] = key === "deploy_here" ? { available: false, reason: { code: "features.hub_only", params: { feature: key }, text: `${key}: only a central that is a hub has this, and this Noust is a server`, hint: null } } : { available: true, reason: null };
  }
  return states as never;
}

/** The session of a Noust that is a server and says what its table does: everything works but what is a hub's. */
export function serverSession(): SessionInfo {
  return { ...SESSION, central: { role: "server", sealed: false, locked: false, deploy_here: "", features: serverTable() } };
}

/** The session of a hub told, in its compose file, to deploy containers on its machine's Docker. */
export function nasSession(): SessionInfo {
  return { ...SESSION, hostname: "nas-casa", permissions: [...(SESSION.permissions ?? []), "docker.read", "docker.manage"], central: { role: "hub", sealed: false, locked: false, deploy_here: "containers", features: tableOn() } };
}

/** The session of a hub that was not told to: the machine only has Docker, and the console is what it always was. */
export function hubSession(): SessionInfo {
  return { ...SESSION, hostname: "nas-casa", permissions: [...(SESSION.permissions ?? []), "docker.read", "docker.manage"], central: { role: "hub", sealed: false, locked: false, deploy_here: "", features: tableOff() } };
}
