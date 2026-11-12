/**
 * What the fleet shares and where each server stands (`/api/fleet/settings`, item 90): the kinds, every setting
 * in every scope, and for each server which of three levels it is on for each setting: it **follows** the fleet,
 * it has an **exception** (a value of its own, set from the central or changed on the server), or it is
 * **independent** (the central pushes it nothing). The status is the server's to say; this reads it.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../../api/client";
import type { BodyOf, ResponseOf } from "../../../api/client";
import type { Status } from "../../../components/ui/StatusPill";
import type { PlainKey } from "../../../i18n";

export type FleetSettingsList = ResponseOf<"/api/fleet/settings", "get">;
export type FleetSetting = FleetSettingsList["items"][number];
export type FleetSettingKind = FleetSettingsList["kinds"][number];
export type SettingSaved = ResponseOf<"/api/fleet/settings/{resource}/{name}", "put">;
export type SettingBody = BodyOf<"/api/fleet/settings/{resource}/{name}", "put">;

/** How one server holds one setting: where its value comes from. */
export type Origin = "fleet" | "central_exception" | "local_exception" | "independent" | "none";

/** Whether what the server holds is what is wanted. */
export type Sync = "in_sync" | "pending" | "diverged" | "release_pending" | "not_applicable" | "unreachable" | "unsupported" | "refused" | "unknown";

/** One server's cell for one setting. */
export interface Cell {
  node: string;
  origin: Origin;
  sync: Sync;
  wanted_version?: number | null;
  applied_version?: number | null;
  state?: string | null;
  applied_by?: string | null;
  applied_at?: string | null;
  change?: string | null;
  restore_requested?: boolean;
  /** Whether a channel's route can match anything on this server. */
  reach?: { reaches: boolean; why?: string | null } | null;
}

export interface StatusSetting {
  resource: string;
  name: string;
  version: number;
  change: string;
  changed_by: string;
  changed_at: string;
  exceptions: { node?: string; version?: number }[];
  servers: Cell[];
}

export interface StatusServer {
  node: string;
  mode: "managed" | "independent";
  outcome: string;
  message?: string | null;
  code?: string | null;
  hint?: string | null;
  pending_look?: boolean;
}

export interface FleetStatus {
  generated_at: string;
  partial: boolean;
  servers: StatusServer[];
  settings: StatusSetting[];
  released: { resource: string; name: string; node?: string }[];
}

export const fleetSettingsKeys = {
  all: ["fleet", "settings"] as const,
  list: ["fleet", "settings", "list"] as const,
  status: ["fleet", "settings", "status"] as const,
};

/** Every kind there is, and every setting the fleet holds, with its secrets shown as `***`. */
export const fleetSettingsQuery = () =>
  queryOptions({
    queryKey: fleetSettingsKeys.list,
    queryFn: ({ signal }) => request("get", "/api/fleet/settings", { signal }),
  });

/** Where each server stands on each setting: each is asked, and one that cannot be read says why. */
export const fleetStatusQuery = () =>
  queryOptions({
    queryKey: fleetSettingsKeys.status,
    queryFn: async ({ signal }) => (await request("get", "/api/fleet/settings/status", { signal })) as unknown as FleetStatus,
    refetchInterval: 30_000,
  });

export function saveSetting(resource: string, name: string, body: SettingBody) {
  return request("put", "/api/fleet/settings/{resource}/{name}", { params: { resource, name }, body });
}

/** Takes a setting away from the fleet (or an exception away from one server): the servers keep what they applied. */
export function removeSetting(resource: string, name: string, scope: string) {
  return request("delete", "/api/fleet/settings/{resource}/{name}", { params: { resource, name }, query: { scope, apply: true } });
}

/** `adopt` takes over what the server kept under the fleet's names when it follows again, instead of leaving it as a conflict. */
export function setServerMode(node: string, mode: "managed" | "independent", adopt = false) {
  return request("put", "/api/fleet/servers/{node}/settings-mode", { params: { node }, body: { mode, adopt } });
}

/** Puts a setting back to the fleet's value on one server where its operator changed it. */
export function restoreOnServer(node: string, resource: string, name: string) {
  return request("post", "/api/fleet/servers/{node}/settings/{resource}/{name}/restore", { params: { node, resource, name } });
}

export interface ApplyPlan {
  nodes: { node: string; state: string; reason?: string | null }[];
  summary: { run: number; skipped: number };
  notes?: unknown[];
}

export interface ApplyAnswer {
  plan: ApplyPlan;
  job: { id: string } | null;
}

/** "Apply to the fleet": what would run (`plan`), or the job that runs it. */
export async function applyToFleet(options: { plan: boolean; adopt?: boolean; restoreAll?: boolean }): Promise<ApplyAnswer> {
  const answer = await request("post", "/api/fleet/settings/apply", { body: { plan: options.plan, adopt: options.adopt ?? false, restore_all: options.restoreAll ?? false } });
  return answer as unknown as ApplyAnswer;
}

/** The level a server is on for a setting, which is what the three words of the page say. */
export type Level = "follows" | "exception" | "independent";

/** A cell, in the console's words: its level, the state it is drawn with and the key of its label. */
export interface CellView {
  level: Level | null;
  /** `null` is a state that is well: it is drawn plain, with no colour (only what is not well is tinted). */
  state: Status | null;
  label: PlainKey;
  /** The server holds a value the fleet did not give it, which can be put back. */
  restorable: boolean;
}

/**
 * How a cell reads. The order is what matters most: a server that cannot be read, or that refuses, is a problem
 * whatever it should hold; an independent one is left alone on purpose; then what the server holds against what
 * is wanted. A setting that does not reach a server (a channel whose route matches nothing there) is not a gap.
 */
export function cellView(cell: Cell): CellView {
  const plain = (level: Level | null, label: PlainKey, state: Status | null = null, restorable = false): CellView => ({ level, state, label, restorable });
  if (cell.sync === "unreachable") return plain(null, "fleetSettings.cell.unreachable", "failed");
  if (cell.sync === "refused") return plain(null, "fleetSettings.cell.refused", "failed");
  if (cell.sync === "unsupported") return plain(null, "fleet.outcome.unsupported", "stopped");
  if (cell.origin === "independent") return plain("independent", "fleetSettings.cell.independent", "stopped");
  if (cell.reach !== null && cell.reach !== undefined && !cell.reach.reaches) return plain(null, "fleetSettings.cell.notSent", "stopped");
  if (cell.sync === "not_applicable") return plain(null, "fleetSettings.cell.notApplicable", "stopped");
  if (cell.origin === "local_exception") {
    return cell.sync === "diverged" || cell.restore_requested === true
      ? plain("exception", cell.restore_requested === true ? "fleetSettings.cell.restoreAsked" : "fleetSettings.cell.changedHere", "warning", true)
      : plain("exception", "fleetSettings.cell.ownValue");
  }
  if (cell.sync === "pending") return plain(cell.origin === "central_exception" ? "exception" : "follows", "fleetSettings.cell.pending", "queued");
  if (cell.sync === "release_pending") return plain(cell.origin === "central_exception" ? "exception" : "follows", "fleetSettings.cell.releasePending", "queued");
  if (cell.sync === "diverged") return plain("follows", "fleetSettings.cell.differs", "warning", true);
  if (cell.sync === "unknown") return plain(null, "fleetSettings.cell.unknown", "stopped");
  if (cell.origin === "central_exception") return plain("exception", "fleetSettings.cell.exception");
  if (cell.origin === "none") return plain(null, "fleetSettings.cell.none", "stopped");
  return plain("follows", "fleetSettings.cell.follows");
}

/** How many cells are not well, for the line over the table and for the button that applies. */
export function pendingCount(status: FleetStatus | undefined): number {
  if (status === undefined) return 0;
  return status.settings.reduce((total, setting) => total + setting.servers.filter((cell) => ["pending", "release_pending", "diverged"].includes(cell.sync)).length, 0);
}

/** The cell of a server in a setting, if the status has one. */
export function cellOf(setting: StatusSetting, node: string): Cell | undefined {
  return setting.servers.find((cell) => cell.node === node);
}

/** The kind of a setting by its key, from the list the API sent. */
export function kindOf(kinds: readonly FleetSettingKind[], key: string): FleetSettingKind | undefined {
  return kinds.find((kind) => kind.key === key);
}
