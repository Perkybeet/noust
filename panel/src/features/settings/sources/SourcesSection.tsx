import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";

import { ErrorBlock, QueryState } from "../../../components/page/QueryState";
import { Section } from "../../../components/page/Section";
import { Button } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Mono } from "../../../components/ui/Mono";
import { ServerText } from "../../../components/ui/ServerText";
import type { CodedText } from "../../../components/ui/ServerText";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { OBJECT_ICONS, VERB_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatDuration } from "../../../lib/format";
import { reportActionError } from "../../apps/useAppActions";
import { failedDeliveriesQuery, renewLeases, repositoriesLeased, retryDelivery, soonest, sourcesKeys, sourcesQuery, stateView } from "./data";
import type { Delivery, NodeSources } from "./data";

function problemOf(node: NodeSources) {
  return node.error ?? node.problems?.[0] ?? null;
}

function columnsFor(t: T): Column<NodeSources>[] {
  return [
    { id: "server", header: t("connections.column.server"), card: "title", cell: (node) => <Mono tone="default">{node.node}</Mono> },
    {
      id: "state",
      header: t("connections.column.state"),
      card: "status",
      cell: (node) => {
        const view = stateView(node.state);
        const problem = problemOf(node);
        return (
          <span className="flex min-w-0 flex-col items-start gap-0.5">
            {view.pill === null ? <span className="text-13 text-fg">{t(view.label)}</span> : <StatusPill state={view.pill} label={t(view.label)} appearance="inline" size="sm" />}
            {problem !== null ? (
              <span className="text-12 text-fg-muted">
                <ServerText coded={problem.coded as unknown as CodedText} text={problem.text} />
              </span>
            ) : null}
          </span>
        );
      },
    },
    {
      id: "tokens",
      header: t("connections.column.tokens"),
      hideBelow: "md",
      cell: (node) => {
        const count = repositoriesLeased(node);
        const ends = soonest(node);
        const uncovered = node.uncovered?.length ?? 0;
        return (
          <span className="flex min-w-0 flex-col text-13 text-fg-muted">
            <span>{count === 0 ? t("connections.tokens.none") : t("connections.tokens.count", { count })}</span>
            {ends !== null ? <span className="text-12">{t("connections.tokens.expires", { time: formatDuration(ends) })}</span> : null}
            {uncovered > 0 ? <span className="text-12">{t("connections.tokens.uncovered", { count: uncovered })}</span> : null}
          </span>
        );
      },
    },
  ];
}

function FailedPushes({ t }: { t: T }) {
  const queryClient = useQueryClient();
  const failed = useQuery(failedDeliveriesQuery());
  const retry = useMutation({
    mutationFn: (id: string) => retryDelivery(id),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: sourcesKeys.all });
    },
    onError: (error) => {
      reportActionError(t("connections.deliveries.retryFailed"), error);
    },
  });
  const rows: readonly Delivery[] = failed.data?.items ?? [];
  if (rows.length === 0) return null;
  const columns: Column<Delivery>[] = [
    { id: "repository", header: t("connections.deliveries.column.repository"), card: "title", cell: (delivery) => <Mono tone="default">{delivery.repository}</Mono> },
    { id: "server", header: t("connections.deliveries.column.server"), cell: (delivery) => <Mono>{delivery.node}</Mono> },
    { id: "state", header: t("connections.deliveries.column.state"), card: "status", cell: () => <StatusPill state="failed" label={t("common.statusPill.failed")} appearance="inline" size="sm" /> },
    { id: "attempts", header: t("connections.deliveries.column.attempts"), hideBelow: "md", mono: true, cell: (delivery) => String(delivery.attempts) },
  ];
  return (
    <DataTable
      caption={t("connections.deliveries.caption")}
      columns={columns}
      rows={rows}
      getRowId={(delivery) => delivery.id}
      density="compact"
      rowActions={(delivery) => (
        <Button size="sm" variant="ghost" loading={retry.isPending && retry.variables === delivery.id} aria-label={t("connections.deliveries.retryLabel", { repository: delivery.repository, server: delivery.node })} onClick={() => retry.mutate(delivery.id)}>
          <span className="max-sm:sr-only">{t("common.words.retry")}</span>
        </Button>
      )}
    />
  );
}

/**
 * What a central adds to Connections: how each server reaches the repositories it deploys (the token the central
 * lent it, when it ends, what keeps it from having one) and the pushes the central passed on, with the ones that
 * did not arrive and a button to try them again. The tokens are renewed by the central before they end; "Renew
 * now" is for the person who has just made the first application of a repository. Only a central has it.
 */
export function SourcesSection() {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery(sourcesQuery());
  const renew = useMutation({
    mutationFn: (node: string | null) => renewLeases(node === null ? undefined : [node]),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: sourcesKeys.all });
    },
  });
  const renewing = renew.isPending ? (renew.variables ?? "") : null;
  return (
    <Section
      title={t("connections.title")}
      icon={OBJECT_ICONS.integration}
      description={t("connections.description")}
      actions={
        <Button size="sm" variant="secondary" icon={<VERB_ICONS.refresh aria-hidden="true" />} loading={renew.isPending && renew.variables === null} onClick={() => renew.mutate(null)}>
          {t("connections.renew.all")}
        </Button>
      }
    >
      <QueryState query={query} label={t("connections.loading")} skeleton={<Skeleton className="h-32 w-full rounded-card" />}>
        {(data) => (
          <div className="flex min-w-0 flex-col gap-4">
            {renew.isError ? <ErrorBlock live compact error={renew.error} title={t("connections.renew.failed")} /> : null}
            <DataTable
              caption={t("connections.tableCaption")}
              columns={columnsFor(t)}
              rows={data.nodes}
              getRowId={(node) => node.node}
              density="compact"
              rowActions={(node) => (
                <Button size="sm" variant="ghost" icon={<VERB_ICONS.refresh aria-hidden="true" />} loading={renewing === node.node} aria-label={t("connections.renew.label", { server: node.node })} onClick={() => renew.mutate(node.node)}>
                  <span className="max-sm:sr-only">{t("connections.renew.one")}</span>
                </Button>
              )}
            />
            <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-13 text-fg-muted">
              <span className="font-medium text-fg">{t("connections.deliveries.title")}</span>
              <span>{t("connections.deliveries.delivered", { count: data.deliveries.delivered })}</span>
              <span>{t("connections.deliveries.pending", { count: data.deliveries.pending })}</span>
              <span>{t("connections.deliveries.failed", { count: data.deliveries.failed })}</span>
              {data.deliveries.expired > 0 ? <span>{t("connections.deliveries.expired", { count: data.deliveries.expired })}</span> : null}
            </p>
            <FailedPushes t={t} />
          </div>
        )}
      </QueryState>
    </Section>
  );
}
