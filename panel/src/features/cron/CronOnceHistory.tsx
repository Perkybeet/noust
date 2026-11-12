import { useQuery } from "@tanstack/react-query";
import { RotateCw } from "lucide-react";
import { useState } from "react";

import { isApiError } from "../../api/client";
import { cronHistoryQuery } from "../../api/queries/cron";
import type { CronOnceRun } from "../../api/queries/cron";
import { RelativeTime } from "../../components/page/RelativeTime";
import { RowMenu } from "../../components/page/RowMenu";
import { ErrorBlock } from "../../components/page/QueryState";
import { Section } from "../../components/page/Section";
import { Badge } from "../../components/ui/Badge";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyState } from "../../components/ui/EmptyState";
import { MenuItem } from "../../components/ui/Menu";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import { useNodeCapability } from "../../nodes/capability";
import { onceWhen, runStatus } from "./data";

export interface CronOnceHistoryProps {
  /** Opens the new-task dialog made like this one, for another date. */
  onAgain: (run: CronOnceRun) => void;
}

/** How a one-time task ended, in the state language of the rest of the page; a run that was late says so beside it. */
function Result({ run }: { run: CronOnceRun }) {
  const t = useT();
  const view = runStatus(run.result);
  return (
    <span className="flex flex-wrap items-center gap-x-1.5 gap-y-0.5">
      <StatusPill state={view.state} label={run.ok ? t("cron.status.succeeded") : view.label} appearance="inline" size="sm" />
      {view.detail !== undefined ? (
        <Mono tone="faint" className="text-12">
          {view.detail}
        </Mono>
      ) : null}
      {run.late ? (
        <span title={t("cron.once.lateHelp")}>
          <Badge>{t("cron.once.late")}</Badge>
        </span>
      ) : null}
    </span>
  );
}

/** What the task printed, the last lines of it: the units that held it are gone, so this is where it is read. */
function Output({ run, onClose }: { run: CronOnceRun | null; onClose: () => void }) {
  const t = useT();
  return (
    <Drawer
      open={run !== null}
      onOpenChange={(next) => {
        if (!next) onClose();
      }}
      title={run === null ? t("cron.once.historyTitle") : t("cron.once.outputTitle", { name: run.name })}
      description={t("cron.once.outputDescription")}
    >
      {run === null ? null : run.output.trim() === "" ? (
        <EmptyState variant="inline" title={t("cron.once.noOutput")} />
      ) : (
        <SystemOutput label={t("cron.runsDrawer.outputLabel", { name: run.name })}>{run.output}</SystemOutput>
      )}
    </Drawer>
  );
}

/**
 * The one-time tasks that ran and retired themselves: when each was asked for (on the server's clock, with the zone),
 * how it ended, whether the server ran it late, and what it printed, kept for thirty days. The units that held the
 * result are gone, so this is the only place it is; "Schedule again" opens the task's form for another date.
 *
 * Only a server that keeps the record has the section. A node before 4.3 answers its route with a 404 (or its schema
 * says it has none), and the section says so in a line instead of failing the page.
 */
export function CronOnceHistory({ onAgain }: CronOnceHistoryProps) {
  const t = useT();
  const capability = useNodeCapability("GET /api/cron/history");
  const history = useQuery({ ...cronHistoryQuery(), enabled: capability.status === "available" || capability.status === "unknown" });
  const [reading, setReading] = useState<CronOnceRun | null>(null);

  if (capability.status === "missing" || (history.isError && isApiError(history.error) && history.error.status === 404)) {
    // This console's own server has the route; only another server, a node of the fleet, can lack it.
    return capability.node === null ? null : <p className="text-13 text-fg-muted">{t("cron.once.notOnServer", { node: capability.node })}</p>;
  }
  if (history.isError) {
    return <ErrorBlock compact error={history.error} title={t("cron.once.historyWhat")} onRetry={() => void history.refetch()} />;
  }
  const items = history.data?.items ?? [];
  // A server that has never run one has nothing to say: the dialog is where the feature is found.
  if (history.data !== undefined && items.length === 0) return null;
  if (history.data === undefined) return null;

  const columns: Column<CronOnceRun>[] = [
    {
      id: "name",
      header: t("cron.once.columns.task"),
      card: "title",
      cell: (run) => (
        <span className="flex min-w-0 flex-col">
          <Mono className="font-medium">{run.name}</Mono>
          <Mono tone="faint" truncate title={run.command} className="max-w-80 text-12 font-normal">
            {run.command}
          </Mono>
        </span>
      ),
    },
    { id: "result", header: t("cron.once.columns.result"), width: "w-52", card: "status", cell: (run) => <Result run={run} /> },
    { id: "due", header: t("cron.once.columns.due"), hideBelow: "md", cell: (run) => <span className="text-fg">{onceWhen(run.run_at, t.locale)}</span> },
    {
      id: "ran",
      header: t("cron.once.columns.ran"),
      width: "w-36",
      cell: (run) => (run.started_at === null || run.started_at === undefined ? <span className="text-fg-muted">-</span> : <RelativeTime value={run.started_at} />),
    },
  ];

  return (
    <>
      <Section title={t("cron.once.historyTitle")} icon={OBJECT_ICONS.cron} description={t("cron.once.historyDescription", { days: history.data.days })}>
        <DataTable
          columns={columns}
          rows={items}
          getRowId={(run) => String(run.id)}
          caption={t("cron.once.historyCaption")}
          density="compact"
          onRowActivate={setReading}
          rowActions={(run) => (
            <RowMenu name={run.name}>
              <MenuItem icon={<OBJECT_ICONS.activity />} onClick={() => setReading(run)}>
                {t("cron.once.viewOutput")}
              </MenuItem>
              <MenuItem icon={<RotateCw />} onClick={() => onAgain(run)}>
                {t("cron.once.again")}
              </MenuItem>
            </RowMenu>
          )}
        />
      </Section>
      <Output run={reading} onClose={() => setReading(null)} />
    </>
  );
}
