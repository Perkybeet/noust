import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode } from "react";

import type { OverviewAttentionReason } from "../../api/queries/overview";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { StatTile } from "../../components/page/StatTile";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, StatusPill, stateTextClass } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatPercent } from "../../lib/format";
import { reasonText } from "../overview/NeedsAttention";
import { useFleetActions } from "./BulkActionDialog";
import {
  ACTION_WORDS,
  attentionOf,
  countsOf,
  fleetJobsQuery,
  isKnownAction,
  jobStatusView,
  noustOf,
  numberOf,
  objectOf,
  originOf,
  outcomeKind,
  textOf,
} from "./data";
import type { AttentionEntry, FleetJob, FleetRow, NodeOutcome, RowOrigin } from "./data";
import { ServerLink } from "./links";
import { OutcomePill, PartialNotice, ServerCell, useFleetView } from "./parts";

/** How many items "Needs attention" shows before "Show all". */
const ATTENTION_SHOWN = 5;

interface FleetAttention {
  origin: RowOrigin;
  item: AttentionEntry;
}

/** Where an attention item is fixed, on its own server. */
function subjectPath(item: AttentionEntry): string {
  const kind = item.subject["kind"];
  const domain = textOf(item.subject["domain"]);
  const name = textOf(item.subject["name"]);
  if (kind === "app" && domain !== null) return `/apps/${encodeURIComponent(domain)}`;
  if (kind === "certificate") return "/domains";
  if (kind === "unit" && name !== null) return `/server/services/${encodeURIComponent(name)}`;
  return "/server";
}

function AttentionItemRow({ t, entry }: { t: T; entry: FleetAttention }) {
  const { origin, item } = entry;
  const state = item.severity === "fail" ? "failed" : "warning";
  return (
    <li className="flex min-w-0 gap-3 px-4 py-3">
      <span className="flex h-5 shrink-0 items-center">
        <StatusGlyph state={state} size={14} className={stateTextClass(state)} />
        <span className="sr-only">{item.severity === "fail" ? t("fleet.summary.attention.failure") : t("fleet.summary.attention.warning")}</span>
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        <div className="flex min-w-0 flex-wrap items-baseline gap-x-2">
          <ServerLink
            node={origin.local ? null : origin.node}
            path={subjectPath(item)}
            className="min-w-0 truncate rounded-chip text-13 font-medium text-fg hover:underline hover:underline-offset-2"
          >
            <span translate="no">{item.title}</span>
          </ServerLink>
          <span className="text-12 text-fg-muted">{t.rich("fleet.summary.attention.on", { server: <Mono key="server">{origin.node}</Mono> })}</span>
        </div>
        <ul className="flex min-w-0 flex-col">
          {item.reasons.map((reason, index) => (
            <li key={`${String(reason["code"])}-${String(index)}`} className="text-13 text-fg-muted">
              {reasonText(t, reason as unknown as OverviewAttentionReason)}
            </li>
          ))}
        </ul>
      </div>
    </li>
  );
}

function AttentionCard({ t, rows, loading }: { t: T; rows: readonly FleetRow[]; loading: boolean }) {
  const [all, setAll] = useState(false);
  const entries: FleetAttention[] = rows
    .flatMap((row) => (attentionOf(row)?.items ?? []).map((item) => ({ origin: originOf(row), item })))
    .sort((a, b) => (a.item.severity === b.item.severity ? 0 : a.item.severity === "fail" ? -1 : 1));
  const shown = all ? entries : entries.slice(0, ATTENTION_SHOWN);
  let body: ReactNode;
  if (loading) {
    body = (
      <div aria-busy="true" className="flex flex-col gap-2 px-4 py-3.5">
        <span className="sr-only">{t("fleet.summary.attention.loading")}</span>
        <Skeleton className="h-3.5 w-48" />
        <Skeleton className="h-3 w-72 max-w-full" />
      </div>
    );
  } else if (entries.length === 0) {
    body = (
      <div className="flex items-start gap-3 px-4 py-3.5">
        <span className="flex h-5 items-center">
          <StatusGlyph state="running" size={14} className="text-ok" />
        </span>
        <p className="text-13 text-pretty text-fg-muted">{t("fleet.summary.attention.empty", { count: rows.length })}</p>
      </div>
    );
  } else {
    body = (
      <ul className="divide-y divide-border">
        {shown.map((entry) => (
          <AttentionItemRow key={`${entry.origin.node}:${entry.item.id}`} t={t} entry={entry} />
        ))}
      </ul>
    );
  }
  return (
    <Card
      as="section"
      level={2}
      padding="none"
      title={t("fleet.summary.attention.title")}
      description={t("fleet.summary.attention.description")}
      {...(entries.length > ATTENTION_SHOWN
        ? {
            actions: (
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  setAll((value) => !value);
                }}
              >
                {all ? t("fleet.summary.attention.showFewer") : t("fleet.summary.attention.showAll", { count: entries.length })}
              </Button>
            ),
          }
        : {})}
    >
      {body}
    </Card>
  );
}

function jobServers(job: FleetJob): number {
  return Object.values(job.summary).reduce((sum, count) => sum + count, 0);
}

/** The bulk actions run lately, newest first, each opening its job. */
function RecentActions({ t }: { t: T }) {
  const jobs = useQuery(fleetJobsQuery(5));
  const actions = useFleetActions();
  let body: ReactNode;
  if (jobs.isPending) {
    body = (
      <div aria-busy="true" className="flex flex-col gap-2 px-4 py-3.5">
        <span className="sr-only">{t("fleet.summary.actions.loading")}</span>
        <Skeleton className="h-3.5 w-56" />
        <Skeleton className="h-3.5 w-40" />
      </div>
    );
  } else if (jobs.isError) {
    body = <ErrorBlock compact error={jobs.error} title={t("fleet.summary.actions.loadFailed")} onRetry={() => void jobs.refetch()} className="m-4" />;
  } else if (jobs.data.jobs.length === 0) {
    body = (
      <EmptyState
        variant="inline"
        title={t("fleet.summary.actions.empty")}
        action={
          <Button
            size="sm"
            variant="ghost"
            onClick={() => {
              actions.open();
            }}
          >
            {t("fleet.page.runAction")}
          </Button>
        }
        className="px-4"
      />
    );
  } else {
    body = (
      <ul className="divide-y divide-border">
        {jobs.data.jobs.map((job) => {
          const view = jobStatusView(job.status);
          const label = isKnownAction(job.action) ? t(ACTION_WORDS[job.action].label) : job.title;
          return (
            <li key={job.job_id} className="flex min-w-0 items-center justify-between gap-3 px-4 py-2.5">
              <div className="flex min-w-0 flex-col gap-0.5">
                <Link
                  to="/fleet/jobs/$id"
                  params={{ id: job.job_id }}
                  className="min-w-0 truncate rounded-chip text-13 font-medium text-fg hover:underline hover:underline-offset-2"
                >
                  {label}
                </Link>
                <span className="flex flex-wrap items-center gap-x-2 text-12 text-fg-muted">
                  <span>{t("fleet.summary.actions.servers", { count: jobServers(job) })}</span>
                  <span aria-hidden="true">·</span>
                  <RelativeTime value={job.created_at} />
                </span>
              </div>
              <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" className="shrink-0" />
            </li>
          );
        })}
      </ul>
    );
  }
  return (
    <Card as="section" level={2} padding="none" title={t("fleet.summary.actions.title")} description={t("fleet.summary.actions.description")}>
      {body}
    </Card>
  );
}

function Reading({ t, value }: { t: T; value: number | null }) {
  if (value === null) return <EmptyCell reason={t("fleet.summary.notRead")} />;
  return <span className="text-13 text-fg tabular-nums">{formatPercent(value, t.locale)}</span>;
}

function machineReading(row: FleetRow, key: "cpu" | "memory" | "disk"): number | null {
  const machine = objectOf(row["machine"]);
  if (machine === null) return null;
  if (key === "cpu") return numberOf(machine["cpu_percent"]);
  return numberOf(objectOf(machine[key])?.["percent"]);
}

function serverColumns(t: T, outcomes: ReadonlyMap<string, NodeOutcome>): Column<FleetRow>[] {
  return [
    {
      id: "server",
      header: t("fleet.column.server"),
      card: "title",
      sortValue: (row) => (originOf(row).local ? "" : originOf(row).node),
      cell: (row) => <ServerCell origin={originOf(row)} outcome={outcomes.get(originOf(row).node)} />,
    },
    {
      id: "state",
      header: t("fleet.column.state"),
      card: "status",
      width: "w-40",
      cell: (row) => {
        const outcome = outcomes.get(originOf(row).node);
        return outcome === undefined ? <EmptyCell reason={t("fleet.summary.notRead")} /> : <OutcomePill outcome={outcome} />;
      },
    },
    {
      id: "version",
      header: t("fleet.column.version"),
      hideBelow: "md",
      card: "meta",
      cell: (row) => {
        const noust = noustOf(row);
        return noust.current === null ? <EmptyCell reason={t("fleet.summary.notRead")} /> : <Mono tone="muted">{noust.current}</Mono>;
      },
    },
    {
      id: "apps",
      header: t("fleet.column.apps"),
      card: "meta",
      cell: (row) => {
        const apps = countsOf(row, "apps");
        if (apps === null) return <EmptyCell reason={t("fleet.summary.notRead")} />;
        return (
          <span className="flex flex-wrap items-center gap-x-2 text-13 tabular-nums">
            <span className="text-fg">{t("fleet.summary.appsRunning", { count: apps.running })}</span>
            {apps.failed > 0 ? (
              <span className="inline-flex items-center gap-1 font-medium text-fail">
                <StatusGlyph state="failed" size={10} />
                {t("fleet.summary.appsFailed", { count: apps.failed })}
              </span>
            ) : null}
          </span>
        );
      },
    },
    {
      id: "attention",
      header: t("fleet.column.attention"),
      hideBelow: "sm",
      card: "meta",
      cell: (row) => {
        const attention = attentionOf(row);
        if (attention === null) return <EmptyCell reason={t("fleet.summary.olderNoust")} />;
        if (attention.total === 0) return <span className="text-13 text-fg-muted">{t("fleet.summary.noAttention")}</span>;
        const state = attention.items.some((item) => item.severity === "fail") ? "failed" : "warning";
        return (
          <span className={cx("inline-flex items-center gap-1 text-13 font-medium", stateTextClass(state))}>
            <StatusGlyph state={state} size={10} />
            {t("fleet.summary.attentionCount", { count: attention.total })}
          </span>
        );
      },
    },
    {
      id: "cpu",
      header: t("fleet.column.cpu"),
      align: "end",
      hideBelow: "lg",
      card: "hidden",
      sortValue: (row) => machineReading(row, "cpu"),
      cell: (row) => <Reading t={t} value={machineReading(row, "cpu")} />,
    },
    {
      id: "memory",
      header: t("fleet.column.memory"),
      align: "end",
      hideBelow: "lg",
      card: "hidden",
      sortValue: (row) => machineReading(row, "memory"),
      cell: (row) => <Reading t={t} value={machineReading(row, "memory")} />,
    },
    {
      id: "disk",
      header: t("fleet.column.disk"),
      align: "end",
      hideBelow: "md",
      card: "hidden",
      sortValue: (row) => machineReading(row, "disk"),
      cell: (row) => <Reading t={t} value={machineReading(row, "disk")} />,
    },
  ];
}

function sum(rows: readonly FleetRow[], read: (row: FleetRow) => number | null): number {
  return rows.reduce((total, row) => total + (read(row) ?? 0), 0);
}

function Worst({ state, children }: { state: "failed" | "warning"; children: ReactNode }) {
  return (
    <span className={cx("inline-flex items-center gap-1 font-medium", stateTextClass(state))}>
      <StatusGlyph state={state} size={10} />
      {children}
    </span>
  );
}

/**
 * The fleet at a glance (T4 on the fleet's scope): its key figures, what needs attention on
 * any server - each item opening where it is fixed, on its own server - beside the bulk
 * actions run lately, then one line per server. It summarises and links: every list it draws
 * has a view of its own.
 */
export function SummaryTab() {
  const t = useT();
  const view = useFleetView("summary");
  const rows = view.data?.items ?? [];
  const nodes = view.data?.nodes ?? [];
  const down = nodes.filter((outcome) => ["unreachable", "stale", "error"].includes(outcomeKind(outcome))).length;
  const failedApps = sum(rows, (row) => countsOf(row, "apps")?.failed ?? null);
  const runningApps = sum(rows, (row) => countsOf(row, "apps")?.running ?? null);
  const failedUnits = sum(rows, (row) => countsOf(row, "units")?.failed ?? null);
  const expiring = sum(rows, (row) => numberOf(row["certificates_expiring"]));
  const updatable = rows.filter((row) => noustOf(row).state === "update_available").length;
  const reboots = rows.filter((row) => objectOf(objectOf(row["server"])?.["reboot"])?.["required"] === true).length;
  const loading = view.isPending;

  if (view.isError && view.data === undefined) {
    return <ErrorBlock error={view.error} title={t("fleet.page.loadFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />;
  }

  const tile = (content: ReactNode): ReactNode => (loading ? <Skeleton className="h-7 w-20" /> : content);
  // Nothing is said about the fleet before it has answered: no "every server answered" on a blank.
  const detail = (content: ReactNode): ReactNode => (loading ? <Skeleton className="h-3 w-24" /> : content);

  return (
    <div className="flex min-w-0 flex-col gap-8">
      <PartialNotice view={view.data} />
      <div data-slot="figures" className="grid min-w-0 grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-6">
        <StatTile
          label={t("fleet.summary.figures.servers")}
          value={tile(t("fleet.summary.figures.serversValue", { answering: nodes.length - down, total: nodes.length }))}
          detail={detail(down > 0 ? <Worst state="failed">{t("fleet.summary.figures.serversDown", { count: down })}</Worst> : t("fleet.summary.figures.serversAll"))}
        />
        <StatTile
          label={t("fleet.summary.figures.apps")}
          value={tile(t("fleet.summary.appsRunning", { count: runningApps }))}
          detail={detail(failedApps > 0 ? <Worst state="failed">{t("fleet.summary.appsFailed", { count: failedApps })}</Worst> : t("fleet.summary.figures.noneFailed"))}
        />
        <StatTile
          label={t("fleet.summary.figures.services")}
          value={tile(failedUnits > 0 ? t("fleet.summary.figures.servicesFailed", { count: failedUnits }) : t("fleet.summary.figures.noneFailed"))}
          detail={t("fleet.summary.figures.servicesDetail")}
        />
        <StatTile
          label={t("fleet.summary.figures.certificates")}
          value={tile(expiring > 0 ? t("fleet.summary.figures.certificatesExpiring", { count: expiring }) : t("fleet.summary.figures.certificatesNone"))}
          detail={t("fleet.summary.figures.certificatesDetail")}
        />
        <StatTile
          label={t("fleet.summary.figures.updates")}
          value={tile(updatable > 0 ? t("fleet.summary.figures.updatesAvailable", { count: updatable }) : t("fleet.summary.figures.updatesNone"))}
          detail={detail(reboots > 0 ? <Worst state="warning">{t("fleet.summary.figures.reboots", { count: reboots })}</Worst> : t("fleet.summary.figures.noReboots"))}
        />
        <StatTile
          label={t("fleet.summary.figures.attention")}
          value={tile(t("fleet.summary.figures.attentionValue", { count: sum(rows, (row) => attentionOf(row)?.total ?? null) }))}
          detail={t("fleet.summary.figures.attentionDetail")}
        />
      </div>
      <div className="grid min-w-0 items-start gap-6 lg:grid-cols-2">
        <AttentionCard t={t} rows={rows} loading={loading} />
        <RecentActions t={t} />
      </div>
      <Card as="section" level={2} padding="none" title={t("fleet.summary.servers.title")} description={t("fleet.summary.servers.description")}>
        <DataTable
          caption={t("fleet.summary.servers.caption")}
          columns={serverColumns(t, view.outcomes)}
          rows={rows}
          getRowId={(row) => originOf(row).node}
          loading={loading}
          skeletonRows={3}
          mobile="cards"
          className="rounded-none border-0 shadow-none"
        />
      </Card>
      <CommandHint label={t("fleet.page.fromTerminal")} command="noust fleet status" />
    </div>
  );
}
