import { useQuery } from "@tanstack/react-query";

import { api, isApiError } from "../../api/client";
import type { components } from "../../api/schema.gen";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { Disclosure } from "../../components/ui/Disclosure";
import { PlanPreview, planFrom } from "../../components/ui/PlanPreview";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { planPath } from "./planOperations";
import type { PlannedName } from "./planOperations";

export interface PlanDisclosureProps {
  /** The operation the dialog is about to apply. */
  operation: PlannedName;
  /** The values of its path (`{ domain: "shop.example.com" }`). */
  params: Readonly<Record<string, string | number>>;
  /** What the operation would be sent: the simulation takes the same body and ignores what it does not use. */
  body?: Readonly<Record<string, unknown>>;
  /** Opened from the start, for a place that is only about looking at it. */
  defaultOpen?: boolean;
}

/** A route the server does not have: a node that runs an older Noust. */
function isMissing(error: unknown): boolean {
  return isApiError(error) && (error.status === 404 || error.status === 405);
}

/**
 * "See what it will do" for a person's dialog (item 183): Noust's own simulation of the operation the dialog is about to apply,
 * read when the dialog opens and drawn folded under what the dialog says. It is asked for only where the API says the operation has
 * a simulation (`planOperations.ts`, held to the API's description by a test), and it is **not drawn at all** where the server
 * answers that it has none (a server that runs an older Noust): an option that does not exist is not offered, not shown empty. A
 * failure of the simulation is said where it is, with the system's words, and never keeps the person from applying what they came to
 * apply: the plan is to look at, not a step.
 */
export function PlanDisclosure({ operation, params, body = {}, defaultOpen = false }: PlanDisclosureProps) {
  const t = useT();
  const path = planPath(operation, params);
  const query = useQuery({
    queryKey: ["plans", operation, params, body] as const,
    queryFn: () => api<components["schemas"]["PlanOutModel"]>("POST", path, body),
    staleTime: 0,
    retry: false,
  });
  if (query.isError && isMissing(query.error)) return null;
  if (query.data !== undefined) return <PlanPreview plan={planFrom(query.data)} {...(defaultOpen ? { defaultOpen: true } : {})} />;
  if (query.isError) {
    return (
      <Disclosure variant="card" label={t("plans.preview.title")} defaultOpen>
        <ErrorBlock compact error={query.error} title={t("plans.preview.failed")} onRetry={() => void query.refetch()} retrying={query.isFetching} />
      </Disclosure>
    );
  }
  return (
    <LoadingRegion label={t("plans.preview.loading")}>
      <Skeleton className="h-control-md w-full" />
    </LoadingRegion>
  );
}
