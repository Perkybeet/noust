import { useQuery } from "@tanstack/react-query";

import { deploymentLogQuery, deploymentsQuery } from "../../api/queries/deployments";
import type { Deployment } from "../../api/queries/deployments";
import type { Job } from "../../api/queries/jobs";
import { useNow } from "../../components/page/clock";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { LogViewer } from "../../components/ui/LogViewer";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { parseTimestamp } from "../../lib/format";
import { buildLogEvents, jobEvents, logClockText, logSpan, mergeEvents, useLogLines } from "./deployments/buildLog";
import { PhaseTimeline } from "./deployments/PhaseTimeline";
import { currentPhase, logClockOffset, timeline } from "./deployments/phases";
import { useDeploymentJob } from "./deployments/useDeploymentJob";
import { jobStep } from "./useAppJob";
import { stepWords } from "./stepWords";

/** How many of the newest deployments are read to find the one this job is writing. */
const WINDOW = 5;

/** The row of the deploy this job is running, once the deployer has written it (its build is already streaming then). */
export function useDeploymentOf(domain: string, job: Job): Deployment | undefined {
  const history = useQuery({
    ...deploymentsQuery({ domain, limit: WINDOW }),
    staleTime: 0,
    // Until the row appears: the deployer writes it a moment after the job starts.
    refetchInterval: (query) => (query.state.data?.items.some((item) => item.job_id === job.id) ? false : 1_000),
  });
  return history.data?.items.find((item) => item.job_id === job.id);
}

/**
 * What the page of an application says while its first deploy runs (item 99: one way to start an application, and
 * you can watch it): where the deploy is, its steps, and the build's own output as it is written. An application
 * that has never been deployed has no figures, versions or logs to draw yet, so this is all its page holds, and the
 * task centre follows the job if the operator goes elsewhere. The steps and the log are those of the deployment's own
 * page; they are drawn here, not linked to, because the operator has nothing else to look at.
 */
export function FirstDeploy({ domain, job }: { domain: string; job: Job }) {
  const t = useT();
  const deployment = useDeploymentOf(domain, job);
  const words = stepWords(job.type, jobStep(job), t.locale);
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <SurfaceSection title={t("appPages.firstDeploy.title")} icon={OBJECT_ICONS.deployment} description={t("appPages.firstDeploy.line")}>
        {deployment === undefined ? (
          // The row is a moment away: its room is kept, so the steps do not push the log down when they arrive.
          <div className="flex flex-col gap-2" role="status" aria-label={t("appPages.firstDeploy.starting")}>
            <p className="text-13 text-fg-muted">{words ?? t("appPages.firstDeploy.starting")}</p>
            <Skeleton className="h-16 w-full" />
          </div>
        ) : (
          <Steps domain={domain} deployment={deployment} />
        )}
      </SurfaceSection>
      {deployment !== undefined ? <Output domain={domain} deployment={deployment} /> : null}
    </div>
  );
}

function Steps({ deployment }: { domain: string; deployment: Deployment }) {
  const job = useDeploymentJob(deployment);
  const log = useQuery({ ...deploymentLogQuery(deployment.id, null), refetchInterval: 1_000 });
  const lines = useLogLines(log.data?.content);
  const now = useNow(() => 1_000);
  const events = mergeEvents(buildLogEvents(lines), jobEvents(job.entries));
  const span = logSpan([...lines.map((line) => line.at), ...job.entries.map((entry) => entry.at)]);
  const phases = timeline(events, "running", { lastAt: span.last, now: new Date(now), offset: logClockOffset(span.first, parseTimestamp(deployment.started_at)) }, { checksHealth: true });
  const current = currentPhase(phases);
  return (
    <div data-phase={current?.key ?? "start"}>
      <PhaseTimeline phases={phases} outcome="running" />
    </div>
  );
}

function Output({ domain, deployment }: { domain: string; deployment: Deployment }) {
  const t = useT();
  const log = useQuery({ ...deploymentLogQuery(deployment.id, null), refetchInterval: 1_000 });
  const lines = useLogLines(log.data?.content);
  const job = useDeploymentJob(deployment);
  const fallback = job.entries.map((entry, index) => ({ id: index, text: entry.text, ...(entry.at ? { ts: logClockText(entry.at) } : {}) }));
  const shown = lines.length > 0 ? lines : fallback;
  return (
    <SurfaceSection title={t("appPages.firstDeploy.logTitle")} icon={OBJECT_ICONS.log} description={t("appPages.firstDeploy.logLine")} flush>
      <div className="h-editor">
        <LogViewer
          lines={shown}
          height="fill"
          label={t("appPages.deployments.page.logLabel", { id: String(deployment.id), domain })}
          filename={`${domain}-deployment-${String(deployment.id)}.log`}
          emptyMessage={t("appPages.deployments.page.logWaitingFirstLine")}
        />
      </div>
    </SurfaceSection>
  );
}
