import { useQuery } from "@tanstack/react-query";

import { cronLedgerQuery, cronRunsQuery } from "../../api/queries/cron";
import type { CronLedger } from "../../api/queries/cron";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Card } from "../../components/ui/Card";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { SkeletonRows } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { Disclosure } from "../../components/ui/Disclosure";

function runView(success: boolean | null, t: T): { state: "running" | "failed" | "unknown"; label: string } {
  if (success === true) return { state: "running", label: t("cron.status.succeeded") };
  if (success === false) return { state: "failed", label: t("common.words.failedFeminine") };
  return { state: "unknown", label: t("common.statusPill.unknown") };
}

/** The occurrences that did not run on time, or a word that all did; nothing for a job with nothing due. */
function Ledger({ report }: { report: CronLedger }) {
  const t = useT();
  const judged = report.occurrences.length;
  if (!report.enabled || judged === 0) return null;
  const odd = report.occurrences.filter((item) => item.status !== "ran");
  if (odd.length === 0) {
    return <p className="text-13 text-fg-muted">{t("cron.ledger.allRan", { count: judged })}</p>;
  }
  return (
    <Notice tone="warning" title={t("cron.ledger.title", { missed: report.missed, late: report.late })}>
      <p className="text-13">{t("cron.ledger.description")}</p>
      <ul aria-label={t("cron.ledger.listLabel")} className="mt-2 flex flex-col gap-1">
        {odd.map((item) => (
          <li key={item.at} className="flex flex-wrap items-baseline gap-x-2 text-13">
            <RelativeTime value={item.at} className="text-fg" />
            <span className="text-fg-muted">
              {item.status === "late"
                ? t("cron.ledger.late")
                : item.reason === "server_off"
                  ? t("cron.ledger.serverOff")
                  : t("cron.ledger.missed")}
            </span>
          </li>
        ))}
      </ul>
    </Notice>
  );
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
  const ledger = useQuery({ ...cronLedgerQuery(name ?? ""), enabled: name !== null });

  return (
    <Drawer
      open={name !== null}
      onOpenChange={onOpenChange}
      title={name !== null ? t("cron.runsDrawer.titleFor", { name }) : t("cron.runsDrawer.titleDefault")}
      description={t("cron.runsDrawer.description")}
    >
      {name === null ? null : (
        <QueryState
          query={runs}
          label={t("cron.runsDrawer.what")}
          skeleton={<SkeletonRows rows={3} />}
          isEmpty={(data) => data.runs.length === 0}
          empty={
            <div className="flex flex-col gap-3">
              {ledger.data !== undefined ? <Ledger report={ledger.data} /> : null}
              <EmptyState variant="inline" title={t("cron.runsDrawer.empty")} />
            </div>
          }
        >
          {(data) => (
            <div className="flex flex-col gap-3">
              {ledger.data !== undefined ? <Ledger report={ledger.data} /> : null}
              <ul className="flex flex-col gap-2">
                {data.runs.map((run, index) => {
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
                        <div className="mt-2">
                          <Disclosure label={t("cron.runsDrawer.output")}>
                            <div className="rounded-control border border-border bg-bg-sunken px-2.5 py-2">
                              <SystemOutput label={t("cron.runsDrawer.outputLabel", { name })}>{run.output}</SystemOutput>
                            </div>
                          </Disclosure>
                        </div>
                      ) : null}
                    </Card>
                  );
                })}
              </ul>
            </div>
          )}
        </QueryState>
      )}
    </Drawer>
  );
}
