/**
 * Every cleanup that ran on this server: when, who or what started it, how full the fullest disk
 * was before and after, what each action freed or removed, and what failed. A cleanup that took
 * something an operator missed can be reconstructed from here instead of guessed at.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { RelativeTime } from "../../../components/page/RelativeTime";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Drawer } from "../../../components/ui/Drawer";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes, formatPercent } from "../../../lib/format";
import { ServerQueryState } from "../errors";
import { cleanupHistoryQuery } from "../queries";
import type { CleanupRun } from "../queries";
import { cleanupActionLabel } from "./CleanupScheduleCard";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

/** Runs the card keeps room for while they load. */
const TYPICAL_RUNS = 4;

/** What started a run, in the operator's words. */
export function triggerLabel(t: T, trigger: string): string {
  switch (trigger) {
    case "schedule":
      return t("server.storage.history.trigger.schedule");
    case "manual":
      return t("server.storage.history.trigger.manual");
    case "policy":
      return t("server.storage.history.trigger.policy");
    default:
      return trigger;
  }
}

/** How a run ended, as a state: its shape and its word. */
export function runStatus(t: T, status: string): { label: string; state: Status } {
  switch (status) {
    case "ok":
      return { label: t("server.storage.history.status.ok"), state: "running" };
    case "partial":
      return { label: t("server.storage.history.status.partial"), state: "warning" };
    case "failed":
      return { label: t("common.words.failedFeminine"), state: "failed" };
    case "skipped":
      return { label: t("server.storage.history.status.skipped"), state: "stopped" };
    default:
      return { label: status, state: "unknown" };
  }
}

/** The fullest disk before and after: "81% to 62%", or only the first when nothing ran. */
function diskWords(t: T, run: CleanupRun): string | null {
  if (run.percent_before == null) return null;
  const before = formatPercent(run.percent_before, t.locale);
  if (run.percent_after == null) return before;
  return t("server.storage.history.beforeAfter", { before, after: formatPercent(run.percent_after, t.locale) });
}

function RunDrawer({ run, onClose }: { run: CleanupRun; onClose: () => void }) {
  const t = useT();
  const status = runStatus(t, run.status);
  const disk = diskWords(t, run);
  const actions = run.actions ?? [];
  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="md"
      title={t("server.storage.history.detailTitle", { id: run.id })}
      description={triggerLabel(t, run.trigger)}
    >
      <div className="flex flex-col gap-5">
        <p className="flex flex-wrap items-center gap-2 text-13">
          <StatusPill state={status.state} label={status.label} size="sm" />
          <RelativeTime value={run.started_at} />
          {run.actor ? <Mono tone="muted">{run.actor}</Mono> : null}
        </p>
        {disk !== null ? (
          <p className="text-13 text-fg">
            {run.mount ? <Mono>{run.mount}</Mono> : null} {disk}
            {run.freed_bytes != null ? ` - ${t("server.storage.history.freed", { size: formatBytes(run.freed_bytes, t.locale) })}` : ""}
          </p>
        ) : null}
        {/* Why a run did nothing, in Noust's own words. */}
        {run.reason ? <p className="text-13 text-fg-muted">{run.reason}</p> : null}
        {actions.length > 0 ? (
          <ul aria-label={t("server.storage.history.actionsLabel")} className="flex flex-col divide-y divide-border">
            {actions.map((action, index) => {
              const outcome = runStatus(t, action.status);
              const removed = action.removed ?? [];
              return (
                <li key={`${action.action}:${index}`} className="flex min-w-0 flex-col gap-1.5 py-3">
                  <span className="flex flex-wrap items-center gap-2 text-13 text-fg">
                    {cleanupActionLabel(t, action.action)}
                    <StatusPill state={outcome.state} label={outcome.label} appearance="inline" size="sm" />
                    {action.freed_bytes != null ? <span className="text-12 text-fg-muted">{t("server.storage.history.freed", { size: formatBytes(action.freed_bytes, t.locale) })}</span> : null}
                  </span>
                  {removed.length > 0 ? (
                    <ul aria-label={t("server.storage.history.removedLabel", { action: cleanupActionLabel(t, action.action) })} className="flex flex-col gap-0.5 text-12">
                      {removed.map((item) => (
                        <li key={item}>
                          <Mono>{item}</Mono>
                        </li>
                      ))}
                    </ul>
                  ) : null}
                  {/* A failure is the tool's own output, verbatim. */}
                  {action.error ? (
                    <SystemOutput label={t("server.storage.history.failureLabel", { action: cleanupActionLabel(t, action.action) })} className="rounded-control border border-border bg-bg-sunken px-3 py-2">
                      {action.error}
                    </SystemOutput>
                  ) : null}
                </li>
              );
            })}
          </ul>
        ) : null}
      </div>
    </Drawer>
  );
}

/** The history of cleanups, newest first, with one run's detail in a drawer. */
export function CleanupHistory() {
  const t = useT();
  const query = useQuery(cleanupHistoryQuery());
  return (
    <ServerQueryState query={query} label={t("server.storage.history.label")} skeleton={<SkeletonCard lines={TYPICAL_RUNS} />}>
      {(runs) => <CleanupRuns runs={runs} />}
    </ServerQueryState>
  );
}

function CleanupRuns({ runs }: { runs: CleanupRun[] }) {
  const t = useT();
  const [opened, setOpened] = useState<CleanupRun | null>(null);
  const columns: Column<CleanupRun>[] = [
    {
      id: "when",
      header: t("server.storage.history.when"),
      card: "title",
      cell: (row) => <RelativeTime value={row.started_at} />,
    },
    {
      id: "trigger",
      header: t("server.storage.history.why"),
      hideBelow: "sm",
      cell: (row) => triggerLabel(t, row.trigger),
    },
    {
      id: "status",
      header: t("server.storage.history.result"),
      cell: (row) => {
        const status = runStatus(t, row.status);
        return <StatusPill state={status.state} label={status.label} appearance="inline" size="sm" />;
      },
    },
    {
      id: "disk",
      header: t("server.storage.history.disk"),
      mono: true,
      hideBelow: "md",
      cell: (row) => {
        const words = diskWords(t, row);
        return words ?? <EmptyCell reason={t("server.storage.history.noDisk")} />;
      },
    },
    {
      id: "freed",
      header: t("server.storage.history.freedColumn"),
      align: "end",
      mono: true,
      cell: (row) => (row.freed_bytes != null ? formatBytes(row.freed_bytes, t.locale) : <EmptyCell reason={t("server.storage.history.nothingFreed")} />),
    },
  ];
  return (
    <Card level={2} title={t("server.storage.history.title")} icon={OBJECT_ICONS.activity} description={t("server.storage.history.description")} padding="none">
      <DataTable
        columns={columns}
        rows={runs}
        getRowId={(row) => String(row.id)}
        caption={t("server.storage.history.caption")}
        empty={<EmptyState variant="inline" title={t("server.storage.history.empty")} />}
        rowActions={(row) => (
          <Button size="sm" variant="ghost" onClick={() => setOpened(row)} aria-label={t("server.storage.history.detailFor", { id: row.id })}>
            {t("server.storage.history.details")}
          </Button>
        )}
      />
      {opened !== null ? <RunDrawer run={opened} onClose={() => setOpened(null)} /> : null}
    </Card>
  );
}
