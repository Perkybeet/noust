import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Play } from "lucide-react";

import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { RowList } from "../../components/ui/RowList";
import { SkeletonRows } from "../../components/ui/Skeleton";
import { StatusGlyph, StatusPill, stateTextClass } from "../../components/ui/StatusPill";
import { textLinkClassName } from "../../components/ui/TextLink";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { useFleetActions } from "./BulkActionDialog";
import { actionTitle, fleetJobsQuery, jobStatusView } from "./data";
import type { FleetJob } from "./data";

/** How many servers an action ran on, from how its servers ended. */
function jobServers(job: FleetJob): number {
  return Object.values(job.summary).reduce((sum, count) => sum + count, 0);
}

/**
 * What was run on several servers at once, newest first, each opening its job: the fleet's own actions
 * (renewing certificates, applying its settings), which no server's activity says because none of them ran it.
 */
export function RecentActions() {
  const t = useT();
  const jobs = useQuery(fleetJobsQuery(5));
  const actions = useFleetActions();
  return (
    <Section title={t("fleet.summary.actions.title")} icon={OBJECT_ICONS.activity} loading={jobs.isPending}>
      {jobs.isPending ? (
        <LoadingRegion label={t("fleet.summary.actions.loading")}>
          <SkeletonRows rows={2} />
        </LoadingRegion>
      ) : jobs.isError ? (
        <ErrorBlock compact error={jobs.error} title={t("fleet.summary.actions.loadFailed")} onRetry={() => void jobs.refetch()} retrying={jobs.isRefetching} />
      ) : jobs.data.jobs.length === 0 ? (
        <EmptyState
          variant="inline"
          title={t("fleet.summary.actions.empty")}
          action={
            <Button
              icon={<Play aria-hidden="true" />}
              size="sm"
              variant="ghost"
              onClick={() => {
                actions.open();
              }}
            >
              {t("fleet.page.runAction")}
            </Button>
          }
        />
      ) : (
        <RowList aria-label={t("fleet.summary.actions.title")}>
          {jobs.data.jobs.map((job) => {
            const view = jobStatusView(job.status);
            return (
              <RowList.Item key={job.job_id} leading={<StatusGlyph state={view.state} size={14} className={stateTextClass(view.state)} />}>
                <div className="flex min-w-0 items-baseline justify-between gap-3">
                  <Link to="/fleet/jobs/$id" params={{ id: job.job_id }} className={textLinkClassName("ui", "min-w-0 truncate", "row")}>
                    {actionTitle(t, job.action, job.title)}
                  </Link>
                  <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" className="shrink-0" />
                </div>
                <p className="mt-0.5 flex flex-wrap items-baseline gap-x-3 text-12 text-fg-muted">
                  <span>{t("fleet.summary.actions.servers", { count: jobServers(job) })}</span>
                  <RelativeTime value={job.created_at} />
                </p>
              </RowList.Item>
            );
          })}
        </RowList>
      )}
    </Section>
  );
}
