import { useQuery } from "@tanstack/react-query";
import type { QueryClient } from "@tanstack/react-query";
import { useMemo } from "react";

import { isApiError } from "../../api/client";
import { attentionKeys, attentionQuery, ownAttentionQuery } from "../../api/queries/attention";
import { sessionQuery } from "../../api/queries/auth";
import { overviewKeys, overviewQuery } from "../../api/queries/overview";
import type { OverviewAttentionItem } from "../../api/queries/overview";
import { approvalKeys } from "../approvals/api";
import { attentionCounters } from "../overview/attention";
import type { AttentionCounters } from "../overview/attention";

const NONE: readonly OverviewAttentionItem[] = [];

/** Reads again what the list is made of: when the bell is opened it must not say what was true a minute ago. */
export function refreshAttention(queryClient: QueryClient): void {
  for (const queryKey of [attentionKeys.all, overviewKeys.all, approvalKeys.all]) void queryClient.invalidateQueries({ queryKey });
}

/**
 * What is this person's own and a central adds to the lists of the servers it manages: the requests waiting
 * for their decision, a newer Noust and the recipes behind. The server says it (`GET /api/attention?only=own`),
 * each only for whoever may act on it; a server before 4.3 has no such route and has nothing to add.
 */
export function useOwnAttention(enabled = true): readonly OverviewAttentionItem[] {
  const own = useQuery({ ...ownAttentionQuery(), enabled, retry: false });
  return own.data?.items ?? NONE;
}

export interface Attention {
  /** Everything that needs the operator, worst first: the same list on every screen that shows it. */
  items: readonly OverviewAttentionItem[];
  /** How many there are in all: the server may have cut its own list. This is the one number every screen shows. */
  total: number;
  /** What the sidebar counts beside its destinations, from `items`. */
  counters: AttentionCounters;
  /** The first read is still on its way. */
  loading: boolean;
  /** The list could not be read and there is nothing of it to show. */
  failed: boolean;
}

/**
 * What needs the operator: the one list the server joins and ranks (`GET /api/attention`). The bell, Home's
 * "Needs attention", the Server page's slice and the sidebar's counters read it, so they cannot disagree
 * (NV-16), and the console adds nothing to it.
 *
 * A server before 4.3 has no such route (a 404 through the central): its own Overview is what it can say, and it
 * is read in its place, without what only the new list holds.
 */
export function useAttention(enabled = true): Attention {
  const { data: permissions } = useQuery({ ...sessionQuery(), select: (session) => session.permissions });
  const listed = enabled && permissions !== undefined;
  const list = useQuery({ ...attentionQuery(), enabled: listed, retry: false });
  const older = list.isError && isApiError(list.error) && list.error.status === 404;
  const overview = useQuery({ ...overviewQuery(), enabled: listed && older, retry: false });
  return useMemo(() => {
    const items = list.data?.items ?? overview.data?.attention ?? NONE;
    const unanswered = list.data === undefined && overview.data === undefined;
    return {
      items,
      total: list.data?.total ?? overview.data?.attention_total ?? items.length,
      counters: attentionCounters(items),
      loading: listed && unanswered && (list.isPending || (older && overview.isPending)),
      failed: listed && unanswered && (older ? overview.isError : list.isError),
    };
  }, [list.data, list.isPending, list.isError, overview.data, overview.isPending, overview.isError, older, listed]);
}
