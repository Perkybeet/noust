import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { FlaskConical } from "lucide-react";

import { request } from "../../api/client";
import { restoreDrillsQuery } from "../../api/queries/backups";
import type { RestoreDrill } from "../../api/queries/backups";
import { jobKeys } from "../../api/queries/jobs";
import type { Job } from "../../api/queries/jobs";
import { RelativeTime } from "../../components/page/RelativeTime";
import { ErrorBlock } from "../../components/page/QueryState";
import { Subsection } from "../../components/page/Subsection";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { Disclosure } from "../../components/ui/Disclosure";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { olderServer } from "../app/settings/queries";
import { reportActionError } from "../apps/useAppActions";

/** One test: what it found, step by step, in the server's own words. */
function Drill({ drill }: { drill: RestoreDrill }) {
  const t = useT();
  const steps = drill.steps ?? [];
  return (
    <li className="flex min-w-0 flex-col gap-2 px-3 py-3">
      <div className="flex flex-wrap items-center gap-x-2 gap-y-1">
        <Badge tone={drill.ok ? "ok" : "fail"}>{drill.ok ? t("backups.drills.passed") : t("backups.drills.failed")}</Badge>
        <RelativeTime value={drill.started_at} className="text-12 text-fg-muted" />
        <span className="text-12 text-fg-faint">{drill.trigger === "schedule" ? t("backups.drills.byTimer") : t("backups.drills.byHand")}</span>
        {drill.backup_id ? <Mono truncate title={drill.backup_id}>{drill.backup_id}</Mono> : null}
      </div>
      <p className="text-13 text-pretty text-fg">{drill.summary}</p>
      {steps.length > 0 ? (
        <Disclosure label={t("backups.drills.steps", { count: steps.length })} defaultOpen={!drill.ok}>
          <ol aria-label={t("backups.drills.stepsLabel")} className="flex flex-col divide-y divide-border rounded-control border border-border">
            {steps.map((step) => (
              <li key={step.name} className="flex min-w-0 flex-col gap-0.5 px-3 py-2">
                <span className="flex flex-wrap items-center gap-2">
                  <Mono>{step.name}</Mono>
                  <Badge tone={step.skipped ? "neutral" : step.ok ? "ok" : "fail"}>
                    {step.skipped ? t("backups.drills.skipped") : step.ok ? t("backups.drills.stepOk") : t("backups.drills.stepFailed")}
                  </Badge>
                </span>
                <SystemOutput label={t("backups.drills.stepOutput", { name: step.name })} maxHeight="max-h-32">
                  {step.detail}
                </SystemOutput>
              </li>
            ))}
          </ol>
        </Disclosure>
      ) : null}
    </li>
  );
}

/**
 * The restore tests of one application, in its backups drawer: a button to run one now (a job,
 * which loads the newest backup's databases into temporary databases and drops them) and what
 * the last ones found. A scheduled backup runs one on its own every few days; this says how often.
 */
export function RestoreDrillsSection({ domain }: { domain: string }) {
  const t = useT();
  const queryClient = useQueryClient();
  const query = useQuery(restoreDrillsQuery(domain));
  const run = useMutation({
    mutationFn: () => request("post", "/api/apps/{domain}/restore-drills", { params: { domain }, body: { backup_id: null } }),
    onSuccess: (result) => {
      queryClient.setQueryData<Job>(jobKeys.detail(result.job_id), (current) => current ?? (result.job as Job));
      void queryClient.invalidateQueries({ queryKey: jobKeys.active });
      toast.info(t("backups.drills.queued", { domain }), { description: t("backups.drills.queuedDescription") });
    },
    onError: (error) => reportActionError(t("backups.drills.notQueued", { domain }), error),
  });
  const title = t("backups.drills.title");

  // A server older than 3.4 has no restore tests: nothing to offer.
  if (query.isError && olderServer(query.error)) return null;
  const action = (
    <Button size="sm" icon={<FlaskConical aria-hidden="true" />} loading={run.isPending} onClick={() => run.mutate()}>
      {t("backups.drills.run")}
    </Button>
  );
  if (query.isPending) {
    return (
      <Subsection title={title} level={3}>
        <div aria-busy="true">
          <span className="sr-only">{t("backups.drills.loading")}</span>
          <Skeleton className="h-16 w-full rounded-control" />
        </div>
      </Subsection>
    );
  }
  if (query.isError) {
    return (
      <Subsection title={title} level={3}>
        <ErrorBlock compact error={query.error} title={t("backups.drills.readFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      </Subsection>
    );
  }
  const { items, drill_days: every } = query.data;
  return (
    <Subsection title={title} description={every > 0 ? t("backups.drills.descriptionEvery", { count: every }) : t("backups.drills.descriptionOff")} actions={action} level={3}>
      {items.length === 0 ? (
        <EmptyState variant="inline" title={t("backups.drills.empty")} />
      ) : (
        <ul aria-label={title} className="flex flex-col divide-y divide-border rounded-control border border-border">
          {items.map((drill) => (
            <Drill key={drill.id ?? drill.started_at} drill={drill} />
          ))}
        </ul>
      )}
    </Subsection>
  );
}
