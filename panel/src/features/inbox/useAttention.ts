import { useQuery } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { alertKeys, alertRulesQuery } from "../../api/queries/alerts";
import { appsQuery } from "../../api/queries/apps";
import { sessionQuery } from "../../api/queries/auth";
import { overviewKeys, overviewQuery } from "../../api/queries/overview";
import type { OverviewAttentionItem } from "../../api/queries/overview";
import { probeKeys, probesQuery } from "../../api/queries/probes";
import { installedRecipesQuery } from "../../api/queries/recipes";
import { versionQuery } from "../../api/queries/system";
import { approvalKeys, approvalsQuery } from "../approvals/api";
import { alertItems, approvalItems, attentionCounters, mergeAttention, noustItems, probeItems, recipeItems } from "../overview/attention";
import type { AttentionCounters } from "../overview/attention";
import { attentionItems, attentionTotal } from "../overview/overviewData";

/**
 * How often what only polling can know is read again: alerts, probes, requests waiting. Two
 * minutes, because opening the inbox reads them again at once (`refreshAttention`) and an idle page
 * must ask the server for little (e2e/idle-requests.spec.ts).
 */
const POLL_MS = 120_000;

/** The Overview, read for what needs attention a little less often than the Overview page reads itself; the stream's events read it sooner. */
const OVERVIEW_POLL_MS = 60_000;

/** A newer recipe is not an emergency: its list is read at most every ten minutes. */
const RECIPES_STALE_MS = 10 * 60_000;

const NONE: readonly OverviewAttentionItem[] = [];

/** Reads again what the inbox is made of: when it is opened, it must not say what was true two minutes ago. */
export function refreshAttention(queryClient: QueryClient): void {
  for (const queryKey of [overviewKeys.all, alertKeys.rules, probeKeys.all, approvalKeys.all]) void queryClient.invalidateQueries({ queryKey });
}

/**
 * What the console adds to the server's own list: the alerts firing, the probes down, the requests
 * waiting for this person's decision, a newer Noust and the recipes with an update. Each read is
 * asked only by whoever may make it - what a role cannot see is not asked for, and not counted.
 *
 * The recipes are the one read that costs the server something (it compares every installed
 * application with the catalog), so the bell does not ask for it on every page: it counts what the
 * console already holds, and the inbox, once opened, reads it (`deep`).
 *
 * The alerts and the probes are the console's own reads of them (`api/queries`); a selector that a
 * settings page owns replaces `alertItems` and `probeItems` with the same output without touching
 * anything here.
 */
export function useExtraAttention(deep = false): readonly OverviewAttentionItem[] {
  const { data: permissions } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });
  const can = (permission: string): boolean => permissions?.includes(permission) ?? false;
  const readsServer = can("server.read");
  const alerts = useQuery({ ...alertRulesQuery(), enabled: readsServer, refetchInterval: POLL_MS, retry: false });
  const probes = useQuery({ ...probesQuery(), enabled: readsServer, refetchInterval: POLL_MS, retry: false });
  const approvals = useQuery({ ...approvalsQuery("requested"), enabled: permissions !== undefined, refetchInterval: POLL_MS, retry: false });
  const version = useQuery({ ...versionQuery(), enabled: can("server.manage"), retry: false });
  const recipes = useQuery({ ...installedRecipesQuery(), enabled: deep && can("apps.read"), staleTime: RECIPES_STALE_MS, retry: false });
  return useMemo(
    () => [...alertItems(alerts.data), ...probeItems(probes.data), ...approvalItems(approvals.data), ...noustItems(version.data), ...recipeItems(recipes.data)],
    [alerts.data, probes.data, approvals.data, version.data, recipes.data],
  );
}

export interface Attention {
  /** Everything that needs the operator, worst first: the same list on every screen that shows it. */
  items: readonly OverviewAttentionItem[];
  /** How many there are in all: the server may have cut its own list. */
  total: number;
  /** What the sidebar counts beside its destinations, from `items`. */
  counters: AttentionCounters;
  /** The first read of the Overview is still on its way. */
  loading: boolean;
  /** The Overview could not be read and there is nothing of it to show. */
  failed: boolean;
}

/**
 * What needs the operator, from every read that can say so (NV-16): the server's list (the
 * Overview) and what the console adds to it (`useExtraAttention`). One list for the inbox, the
 * Overview's "Needs attention" and the sidebar's counters, so they cannot disagree.
 *
 * The applications are read only when something else already has: they refine one reason of the
 * Overview's own list (a service that runs and answers nothing), and the bell must not add a read of
 * the whole list to every page.
 */
export function useAttention(deep = false): Attention {
  const { data: permissions } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });
  const readsServer = permissions?.includes("server.read") ?? false;
  const overview = useQuery({ ...overviewQuery(), enabled: readsServer, refetchInterval: OVERVIEW_POLL_MS, retry: false });
  const apps = useQuery({ ...appsQuery(), enabled: false });
  const extras = useExtraAttention(deep);
  return useMemo(() => {
    const base = overview.data === undefined ? NONE : attentionItems(overview.data, apps.data?.apps);
    const items = mergeAttention(base, extras);
    return {
      items,
      total: overview.data === undefined ? items.length : attentionTotal(overview.data, base) + extras.length,
      counters: attentionCounters(items),
      loading: readsServer && overview.isPending,
      failed: overview.status === "error" && overview.data === undefined,
    };
  }, [overview.data, overview.isPending, overview.status, apps.data, extras, readsServer]);
}
