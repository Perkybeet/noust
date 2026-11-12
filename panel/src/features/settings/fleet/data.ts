/**
 * What the fleet shares and where each server stands (`/api/fleet/settings`, item 90): the kinds, every setting
 * in every scope, and for each server which of three levels it is on for each setting: it **follows** the fleet,
 * it has an **exception** (a value of its own, set from the central or changed on the server), or it is
 * **independent** (the central pushes it nothing). The status is the server's to say; this reads it.
 */

import { queryOptions } from "@tanstack/react-query";

import { request } from "../../../api/client";
import type { BodyOf, ResponseOf } from "../../../api/client";

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
  /**
   * The central has for this server a version newer than the one applied, whether it answers or not (4.3). A central
   * of 4.2 does not say it: `isWaiting` then reads it from the two versions above.
   */
  waiting?: boolean;
  /**
   * Only on a server that is independent: the `sync` it would have if it followed the fleet again, worked out the way it is for
   * a server that does (`pending` is what it would be sent). The central says it so the person is told what will change before.
   */
  if_followed?: string | null;
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
  /**
   * How many servers of the fleet follow it, out of how many (4.3). The central is never counted. The server says
   * it, once, so the console never recalculates it; a central of 4.2 sends neither and the line is not drawn.
   */
  following?: number | null;
  of?: number | null;
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

/** Makes a server follow the fleet or leave it out; following again can take over what the server kept under the fleet's names (`adopt`). */
export function setServerMode(node: string, mode: "managed" | "independent", adopt = false) {
  return request("put", "/api/fleet/servers/{node}/settings-mode", { params: { node }, body: { mode, adopt: mode === "managed" && adopt } });
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

/** What "Apply" is asked: for every server and every setting, or for some of them (4.3: `only` is `kind/name`). */
export interface ApplyOptions {
  plan: boolean;
  adopt?: boolean;
  restoreAll?: boolean;
  /** Only these servers. */
  nodes?: readonly string[];
  /** Only these settings, as `kind/name`. A central that does not know it applies all of them. */
  only?: readonly string[];
  /** `kind/name` to put back where a server's operator changed it. */
  restore?: readonly string[];
}

/** "Apply to the fleet": what would run (`plan`), or the job that runs it. */
export async function applyToFleet(options: ApplyOptions): Promise<ApplyAnswer> {
  const body = {
    plan: options.plan,
    adopt: options.adopt ?? false,
    restore_all: options.restoreAll ?? false,
    ...(options.nodes !== undefined && options.nodes.length > 0 ? { nodes: [...options.nodes] } : {}),
    ...(options.restore !== undefined && options.restore.length > 0 ? { restore: [...options.restore] } : {}),
    ...(options.only !== undefined && options.only.length > 0 ? { only: [...options.only] } : {}),
  };
  const answer = await request("post", "/api/fleet/settings/apply", { body });
  return answer as unknown as ApplyAnswer;
}

/** How many cells are not well, for the button that applies. */
export function pendingCount(status: FleetStatus | undefined): number {
  if (status === undefined) return 0;
  return status.settings.reduce((total, setting) => total + setting.servers.filter((cell) => ["pending", "release_pending", "diverged"].includes(cell.sync)).length, 0);
}

/** The cell of a server in a setting, if the status has one. */
export function cellOf(setting: StatusSetting, node: string): Cell | undefined {
  return setting.servers.find((cell) => cell.node === node);
}
