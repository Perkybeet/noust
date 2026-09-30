/**
 * What the application pages read beyond `GET /api/apps`: the newest deploy of each app, its
 * live CPU and memory, and its resource limits. Derived here once, so the overview, the
 * applications list and an app's own page agree.
 */

import { skipToken, useQuery } from "@tanstack/react-query";

import { appTypesQuery } from "../../api/queries/apps";
import type { AppList, AppTypeInfo } from "../../api/queries/apps";
import type { DeploymentList } from "../../api/queries/deployments";
import { deploymentsQuery } from "../../api/queries/deployments";
import { metricKeys } from "../../api/queries/metrics";
import type { MetricsSnapshot } from "../../api/queries/metrics";

export type AppInfo = AppList["apps"][number];
export type Deployment = DeploymentList["items"][number];

/**
 * The registry's display name (`DISPLAY_NAME`) for a type, from `GET /api/apps/types` - the
 * one source of truth for what Noust can deploy. Falls back to the raw identifier while the
 * list has not loaded, or for a type the registry no longer knows: the backend's word beats a
 * guess.
 */
export function typeName(types: readonly AppTypeInfo[], type: string): string {
  return types.find((entry) => entry.type === type)?.name ?? type;
}

/**
 * An application type as a person reads it ("Next.js", not `nextjs`), for lists and headers.
 * Null for an app whose type was never recorded.
 */
export function useTypeName(): (type: string | null | undefined) => string | null {
  const types = useQuery({ ...appTypesQuery(), retry: false });
  const list = types.data?.types ?? [];
  return (type) => (type === null || type === undefined || type === "" ? null : typeName(list, type));
}

/**
 * The deployment history the lists read the "last deploy" column from: the newest rows across
 * every app, one request for the whole table. AppInfo carries no last-deploy field; an app
 * whose newest deploy is older than this window shows none.
 */
export const RECENT_DEPLOYS = { limit: 200 } as const;

export const recentDeploysQuery = () => deploymentsQuery(RECENT_DEPLOYS);

/** An app's newest deployment attempt, as `GET /api/apps` carries it on every app. */
export type LastDeployment = NonNullable<AppInfo["last_deployment"]>;

/** The newest deployment of each domain, from a list ordered newest first. */
export function latestDeployByDomain(items: readonly Deployment[]): Map<string, Deployment> {
  const latest = new Map<string, Deployment>();
  for (const item of items) {
    const known = latest.get(item.domain);
    if (known === undefined || item.id > known.id) latest.set(item.domain, item);
  }
  return latest;
}

/** When a deployment last changed: its end if it ended, else its start. */
export function deployMoment(deploy: Pick<Deployment, "finished_at"> & { started_at?: string | null }): string | null {
  return deploy.finished_at ?? deploy.started_at ?? null;
}

/**
 * What the lists say about an app's last deploy: the newest row of the recent history when it
 * is there (it knows when a running one started), else the attempt `GET /api/apps` carries,
 * which also reaches deploys older than the history window.
 */
export function lastDeployOf(app: AppInfo, recent: ReadonlyMap<string, Deployment>): LastDeployment | Deployment | null {
  return recent.get(app.domain) ?? app.last_deployment ?? null;
}

/**
 * The collector's newest sample, as the `metrics` server event carries it. There is no REST
 * read of it: until the first event arrives (a few seconds after the page opens), there is
 * no reading, and the pages say so rather than show zeros.
 */
export function useLatestMetrics(): MetricsSnapshot | undefined {
  const { data } = useQuery<MetricsSnapshot>({ queryKey: metricKeys.latest, queryFn: skipToken, staleTime: Infinity });
  return data;
}

export interface AppReading {
  /** CPU use of the app's unit, percent of one CPU. */
  cpu: number | null;
  /** Memory of the app's unit, bytes. */
  memory: number | null;
}

/** The app's own readings in a snapshot, from its unit's cgroup (`app.<domain>.*`). */
export function appReading(snapshot: MetricsSnapshot | undefined, domain: string): AppReading {
  const cpu = snapshot?.[`app.${domain}.cpu.percent`];
  const memory = snapshot?.[`app.${domain}.mem.bytes`];
  return {
    cpu: typeof cpu === "number" && Number.isFinite(cpu) ? cpu : null,
    memory: typeof memory === "number" && Number.isFinite(memory) ? memory : null,
  };
}

/**
 * Whether the collector reads any application at all. Without cgroup v2 (a container, an old
 * kernel) it reads none, and the CPU and memory columns are left out rather than filled with
 * dashes that suggest the apps are idle.
 */
export function readsApps(snapshot: MetricsSnapshot | undefined): boolean {
  return snapshot !== undefined && Object.keys(snapshot).some((key) => key.startsWith("app."));
}

export interface AppLimits {
  /** MemoryMax of the unit, in bytes. */
  memory: number | null;
  /** CPUQuota of the unit, in percent of one CPU. */
  cpu: number | null;
  /** TasksMax of the unit. */
  tasks: number | null;
}

function positive(value: number | null | undefined): number | null {
  return typeof value === "number" && Number.isFinite(value) && value > 0 ? value : null;
}

/** The resource limits of an app's unit (MemoryMax, CPUQuota, TasksMax); null where it has none. */
export function appLimits(app: AppInfo): AppLimits {
  const memoryMb = positive(app.memory_max_mb);
  return {
    memory: memoryMb === null ? null : memoryMb * 1024 * 1024,
    cpu: positive(app.cpu_quota_percent),
    tasks: positive(app.tasks_max),
  };
}

/**
 * The application a pull request preview was made from, or null for an app of its own.
 *
 * Read defensively: `preview_parent` is recorded in the store (2.2) but only reaches the
 * console once `AppInfo` carries it. Until then every app reads as its own, which is what the
 * lists showed before previews existed.
 */
export function previewParentOf(app: object): string | null {
  const value: unknown = (app as Record<string, unknown>)["preview_parent"];
  return typeof value === "string" && value !== "" ? value : null;
}

/** An app's name, as its directory and unit are named: the domain with dots as dashes. */
export function appNameOf(domain: string): string {
  return domain.replace(/\./g, "-");
}
