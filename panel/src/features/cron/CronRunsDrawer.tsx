import { useQuery } from "@tanstack/react-query";

import { cronRunsQuery } from "../../api/queries/cron";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Card } from "../../components/ui/Card";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { T } from "../../i18n";

function runView(success: boolean | null, t: T): { state: "running" | "failed" | "unknown"; label: string } {
  if (success === true) return { state: "running", label: t("cron.status.succeeded") };
  if (success === false) return { state: "failed", label: t("cron.status.failed") };
  return { state: "unknown", label: t("cron.status.unknown") };
}

export interface CronRunsDrawerProps {
  /** The job whose runs are shown; the drawer is closed when null. */
  name: string | null;
  onOpenChange: (open: boolean) => void;
}

/** A job's recorded executions, newest first, each with its exit code and its own output. */
export function CronRunsDrawer({ name, onOpenChange }: CronRunsDrawerProps) {
  const t = useT();
  const runs = useQuery({ ...cronRunsQuery(name ?? "", 20), enabled: name !== null });

  return (
    <Drawer
      open={name !== null}
      onOpenChange={onOpenChange}
      title={name !== null ? t("cron.runsDrawer.titleFor", { name }) : t("cron.runsDrawer.titleDefault")}
      description={t("cron.runsDrawer.description")}
    >
      {name === null ? null : runs.isError && runs.data === undefined ? (
        <ErrorBlock error={runs.error} title={t("cron.runsDrawer.loadError")} onRetry={() => void runs.refetch()} retrying={runs.isRefetching} />
      ) : runs.data === undefined ? (
        <div aria-busy="true" className="flex flex-col gap-3">
          <span className="sr-only">{t("cron.runsDrawer.loading")}</span>
          {[0, 1, 2].map((i) => (
            <Skeleton key={i} className="h-16 w-full rounded-card" />
          ))}
        </div>
      ) : runs.data.runs.length === 0 ? (
        <EmptyState variant="inline" title={t("cron.runsDrawer.empty")} />
      ) : (
        <ul className="flex flex-col gap-2">
          {runs.data.runs.map((run, index) => {
            const view = runView(run.success, t);
            return (
              <Card as="li" key={`${run.started}-${String(index)}`} padding="sm">
                <div className="flex flex-wrap items-center justify-between gap-x-3 gap-y-1">
                  <div className="flex items-center gap-2">
                    <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" />
                    <RelativeTime value={run.started} className="text-12 text-fg-muted" />
                  </div>
                  <Mono tone="faint" className="text-12">
                    {run.exit_code === null ? t("cron.runsDrawer.noExitCode") : t("cron.runsDrawer.exitCode", { code: run.exit_code })}
                  </Mono>
                </div>
                {run.output.trim() !== "" ? (
                  <details className="mt-2">
                    <summary className="cursor-pointer text-12 text-fg-muted hover:text-fg">{t("cron.runsDrawer.output")}</summary>
                    <div className="mt-1.5 rounded-control border border-border bg-bg-sunken px-2.5 py-2">
                      <SystemOutput label={t("cron.runsDrawer.outputLabel", { name })}>{run.output}</SystemOutput>
                    </div>
                  </details>
                ) : null}
              </Card>
            );
          })}
        </ul>
      )}
    </Drawer>
  );
}
