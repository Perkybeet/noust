/**
 * What the Server area reads from `/api/server`: one query per endpoint, all under the
 * `["server"]` key so a job that changed the machine refreshes every tab at once.
 *
 * On a central with a node selected the same queries go to the node through the proxy
 * (api/nodeScope.ts), and their cache entries are the node's: nothing here knows.
 */

import { keepPreviousData, queryOptions } from "@tanstack/react-query";

import { request } from "../../api/client";
import type { ResponseOf } from "../../api/client";

export type ServerSummary = ResponseOf<"/api/server/summary", "get">;
export type Updates = ResponseOf<"/api/server/updates", "get">;
export type UpdatePackage = Updates["packages"][number];
export type UpdatePlan = ResponseOf<"/api/server/updates/plan", "get">;
export type UpdateRun = ResponseOf<"/api/server/updates/runs", "get">[number];
export type Power = ResponseOf<"/api/server/power", "get">;
export type ScheduledPower = NonNullable<Power["scheduled"]>;
export type Storage = ResponseOf<"/api/server/storage", "get">;
export type Mount = Storage["mounts"][number];
export type Candidate = Storage["candidates"][number];
export type CleanupPlan = ResponseOf<"/api/server/storage/cleanup/plan", "get">;
export type DockerImage = ResponseOf<"/api/server/storage/docker/images", "get">[number];
export type Swap = ResponseOf<"/api/server/swap", "get">;
export type ServerClock = ResponseOf<"/api/server/time", "get">;
export type Identity = ResponseOf<"/api/server/identity", "get">;
export type Processes = ResponseOf<"/api/server/processes", "get">;
export type Journal = ResponseOf<"/api/server/logs", "get">;
export type JournalUnit = ResponseOf<"/api/server/logs/units", "get">[number];
export type Boot = ResponseOf<"/api/server/logs/boots", "get">[number];
export type SecurityOverview = ResponseOf<"/api/server/security", "get">;
export type Checks = ResponseOf<"/api/server/security/checks", "get">;
export type SecurityCheck = Checks["checks"][number];
export type SshStatus = ResponseOf<"/api/server/security/ssh", "get">;
export type FixPlan = ResponseOf<"/api/server/security/ssh/fixes/{fix}", "get">;
export type AccountKeys = ResponseOf<"/api/server/security/ssh/keys", "get">[number];
export type SshKey = AccountKeys["files"][number]["keys"][number];
export type Firewall = ResponseOf<"/api/server/security/firewall", "get">;
export type FirewallRule = Firewall["firewall"]["rules"][number];
export type ListeningPort = Firewall["ports"][number];
export type Fail2ban = ResponseOf<"/api/server/security/fail2ban", "get">;
export type PendingChange = ResponseOf<"/api/server/security/changes", "get">[number];

/** The capability every Server tab but Services needs from the server on screen (a 3.0 node lacks it). */
export const SERVER_CAPABILITY = "/api/server/summary";

export type UpdateScope = "security" | "all";
/** What the server sorts the processes by; CPU and memory busiest first, PID and name in order. */
export type ProcessSort = "cpu" | "memory" | "pid" | "name";

export interface JournalFilters {
  unit?: string | undefined;
  priority?: string | undefined;
  since?: string | undefined;
  q?: string | undefined;
  boot?: number | undefined;
  kernel?: boolean | undefined;
  lines?: number | undefined;
}

export const serverKeys = {
  all: ["server"] as const,
  summary: ["server", "summary"] as const,
  updates: ["server", "updates"] as const,
  updatePlan: (scope: UpdateScope, full: boolean) => ["server", "updates", "plan", { scope, full }] as const,
  updateRuns: ["server", "updates", "runs"] as const,
  restartPlan: ["server", "updates", "restarts"] as const,
  power: ["server", "power"] as const,
  storage: ["server", "storage"] as const,
  cleanupPlan: (action: string, sizeMb: number | null) => ["server", "storage", "cleanup-plan", { action, sizeMb }] as const,
  dockerImages: ["server", "storage", "docker-images"] as const,
  swap: ["server", "swap"] as const,
  time: ["server", "time"] as const,
  identity: ["server", "identity"] as const,
  processes: (sort: ProcessSort, byUnit: boolean, limit: number) => ["server", "processes", { sort, byUnit, limit }] as const,
  journal: (filters: JournalFilters) => ["server", "logs", filters] as const,
  journalUnits: ["server", "logs", "units"] as const,
  boots: ["server", "logs", "boots"] as const,
  security: ["server", "security"] as const,
  checks: ["server", "security", "checks"] as const,
  ssh: ["server", "security", "ssh"] as const,
  sshFix: (fix: string) => ["server", "security", "ssh", "fix", fix] as const,
  sshKeys: ["server", "security", "ssh", "keys"] as const,
  firewall: ["server", "security", "firewall"] as const,
  fail2ban: ["server", "security", "fail2ban"] as const,
  changes: ["server", "security", "changes"] as const,
};

/** How often a summary with facts still being computed is read again. */
const SUMMARY_PENDING_POLL_MS = 3_000;

/** True while the summary's slow facts (updates, clock, swap) have not been computed yet. */
export function summaryPending(summary: ServerSummary | undefined): boolean {
  if (summary === undefined) return false;
  const updates = summary.updates.supported && summary.updates.pending == null && summary.updates.error == null;
  const clock = summary.time.synchronized == null && summary.time.error == null;
  return updates || clock;
}

/** The server in one cheap answer: what the header, the overview and the tabs' counts read. */
export const summaryQuery = () =>
  queryOptions({
    queryKey: serverKeys.summary,
    queryFn: ({ signal }) => request("get", "/api/server/summary", { signal }),
    refetchInterval: (query) => (summaryPending(query.state.data) ? SUMMARY_PENDING_POLL_MS : false),
  });

export const updatesQuery = () =>
  queryOptions({
    queryKey: serverKeys.updates,
    queryFn: ({ signal }) => request("get", "/api/server/updates", { signal }),
  });

/** What applying would do: read when the operator opens the dialog, never cached for long. */
export const updatePlanQuery = (scope: UpdateScope, full: boolean) =>
  queryOptions({
    queryKey: serverKeys.updatePlan(scope, full),
    queryFn: ({ signal }) => request("get", "/api/server/updates/plan", { query: { scope, full }, signal }),
    staleTime: 0,
  });

/** Which outdated services a restart would restart, in order, and which wait for a reboot. */
export const restartPlanQuery = () =>
  queryOptions({
    queryKey: serverKeys.restartPlan,
    queryFn: ({ signal }) => request("get", "/api/server/updates/restarts", { signal }),
    staleTime: 0,
  });

export const updateRunsQuery = () =>
  queryOptions({
    queryKey: serverKeys.updateRuns,
    queryFn: ({ signal }) => request("get", "/api/server/updates/runs", { query: { limit: 5 }, signal }),
  });

/** The schedule and the pre-checks: asked when somebody is about to reboot, not polled. */
export const powerQuery = () =>
  queryOptions({
    queryKey: serverKeys.power,
    queryFn: ({ signal }) => request("get", "/api/server/power", { signal }),
    staleTime: 0,
  });

export const storageQuery = () =>
  queryOptions({
    queryKey: serverKeys.storage,
    queryFn: ({ signal }) => request("get", "/api/server/storage", { signal }),
  });

export const cleanupPlanQuery = (action: string, sizeMb: number | null) =>
  queryOptions({
    queryKey: serverKeys.cleanupPlan(action, sizeMb),
    queryFn: ({ signal }) =>
      request("get", "/api/server/storage/cleanup/plan", { query: { action, ...(sizeMb !== null ? { size_mb: sizeMb } : {}) }, signal }),
    staleTime: 0,
  });

export const dockerImagesQuery = () =>
  queryOptions({
    queryKey: serverKeys.dockerImages,
    queryFn: ({ signal }) => request("get", "/api/server/storage/docker/images", { signal }),
  });

export const swapQuery = () =>
  queryOptions({
    queryKey: serverKeys.swap,
    queryFn: ({ signal }) => request("get", "/api/server/swap", { signal }),
  });

export const timeQuery = () =>
  queryOptions({
    queryKey: serverKeys.time,
    queryFn: ({ signal }) => request("get", "/api/server/time", { signal }),
  });

export const identityQuery = () =>
  queryOptions({
    queryKey: serverKeys.identity,
    queryFn: ({ signal }) => request("get", "/api/server/identity", { signal }),
  });

export const processesQuery = (sort: ProcessSort, byUnit: boolean, limit: number) =>
  queryOptions({
    // The limit is in the key: without it, "Show 50" read the cached first ten again (item 55).
    queryKey: serverKeys.processes(sort, byUnit, limit),
    queryFn: ({ signal }) =>
      request("get", "/api/server/processes", { query: { sort_by: sort, limit, ...(byUnit ? { group: "unit" } : {}) }, signal }),
    // The rows already listed stay while the longer list or another order loads.
    placeholderData: keepPreviousData,
  });

export const journalQuery = (filters: JournalFilters) =>
  queryOptions({
    queryKey: serverKeys.journal(filters),
    queryFn: ({ signal }) =>
      request("get", "/api/server/logs", {
        query: {
          lines: filters.lines ?? 300,
          ...(filters.unit !== undefined ? { unit: filters.unit } : {}),
          ...(filters.priority !== undefined ? { priority: filters.priority } : {}),
          ...(filters.since !== undefined ? { since: filters.since } : {}),
          ...(filters.q !== undefined ? { q: filters.q } : {}),
          ...(filters.boot !== undefined ? { boot: filters.boot } : {}),
          ...(filters.kernel === true ? { kernel: true } : {}),
        },
        signal,
      }),
  });

export const journalUnitsQuery = () =>
  queryOptions({
    queryKey: serverKeys.journalUnits,
    queryFn: ({ signal }) => request("get", "/api/server/logs/units", { signal }),
    staleTime: 60_000,
  });

export const bootsQuery = () =>
  queryOptions({
    queryKey: serverKeys.boots,
    queryFn: ({ signal }) => request("get", "/api/server/logs/boots", { signal }),
    staleTime: 5 * 60_000,
  });

/** How often the security summary is read while the checks run in the background. */
const CHECKING_POLL_MS = 2_000;

/**
 * The Security tab's summary, from the last checks. Reading it starts them in the background
 * when there is no report or it is due (`checking`); it is read again until they end.
 */
export const securityOverviewQuery = () =>
  queryOptions({
    queryKey: serverKeys.security,
    queryFn: ({ signal }) => request("get", "/api/server/security", { signal }),
    refetchInterval: (query) => (query.state.data?.checking === true ? CHECKING_POLL_MS : false),
  });

export const checksQuery = () =>
  queryOptions({
    queryKey: serverKeys.checks,
    queryFn: ({ signal }) => request("get", "/api/server/security/checks", { signal }),
  });

export const sshQuery = () =>
  queryOptions({
    queryKey: serverKeys.ssh,
    queryFn: ({ signal }) => request("get", "/api/server/security/ssh", { signal }),
  });

export const sshFixQuery = (fix: string) =>
  queryOptions({
    queryKey: serverKeys.sshFix(fix),
    queryFn: ({ signal }) => request("get", "/api/server/security/ssh/fixes/{fix}", { params: { fix }, signal }),
    staleTime: 0,
  });

export const sshKeysQuery = () =>
  queryOptions({
    queryKey: serverKeys.sshKeys,
    queryFn: ({ signal }) => request("get", "/api/server/security/ssh/keys", { signal }),
  });

export const firewallQuery = () =>
  queryOptions({
    queryKey: serverKeys.firewall,
    queryFn: ({ signal }) => request("get", "/api/server/security/firewall", { signal }),
  });

export const fail2banQuery = () =>
  queryOptions({
    queryKey: serverKeys.fail2ban,
    queryFn: ({ signal }) => request("get", "/api/server/security/fail2ban", { signal }),
  });

/** How often the changes are read while one waits for confirmation: its timer may undo it. */
const PENDING_POLL_MS = 5_000;

/** Changes to sshd and the firewall; a pending one is read again until it is settled. */
export const changesQuery = () =>
  queryOptions({
    queryKey: serverKeys.changes,
    queryFn: ({ signal }) => request("get", "/api/server/security/changes", { signal }),
    refetchInterval: (query) => ((query.state.data ?? []).some((change) => change.status === "pending") ? PENDING_POLL_MS : false),
  });

/** A day the API sends as `2029-05-31` (an end of support), as a Date at its start; anything else as `parseTimestamp` reads it. */
export function dayOf(value: string | null | undefined): Date | null {
  if (value == null) return null;
  const match = /^(\d{4})-(\d{2})-(\d{2})$/.exec(value);
  if (match === null) return null;
  return new Date(Number(match[1]), Number(match[2]) - 1, Number(match[3]));
}
