import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronLeft, FileX } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { isApiError } from "../../../api/client";
import { appQuery } from "../../../api/queries/apps";
import { deploymentLogQuery, deploymentQuery } from "../../../api/queries/deployments";
import type { Deployment } from "../../../api/queries/deployments";
import { useDocumentTitle } from "../../../app/documentTitle";
import { DeployStatePill } from "../../../components/page/AppStatePill";
import { useNow } from "../../../components/page/clock";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { useAnnounceChange } from "../../../components/page/useAnnounceChange";
import { Button } from "../../../components/ui/Button";
import { CopyButton } from "../../../components/ui/CopyButton";
import { EmptyState } from "../../../components/ui/EmptyState";
import { LogViewer } from "../../../components/ui/LogViewer";
import type { LogLine } from "../../../components/ui/LogViewer";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes, formatDuration, parseTimestamp } from "../../../lib/format";
import { hasUnit } from "../../apps/AppRowActions";
import { buildLogEvents, jobEvents, logClockText, logSpan, mergeEvents, useLogLines } from "./buildLog";
import { DeploymentActions } from "./DeploymentActions";
import { PhaseTimeline, phaseDoing } from "./PhaseTimeline";
import { currentPhase, logClockOffset, outcomeOf, timeline } from "./phases";
import type { PhaseKey, PhaseView } from "./phases";
import { useDeploymentJob } from "./useDeploymentJob";
import { shortCommit, triggerWords } from "./words";

const RUNNING = new Set(["queued", "running"]);

/** Bytes of the captured log read by default: the backend's own default tail. */
const DEFAULT_TAIL = 512 * 1024;
/** Bytes asked for when the operator wants the whole log. */
const WHOLE_LOG = 64 * 1024 * 1024;

const LINK =
  "rounded-[4px] text-13 font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus";

/** What went wrong, by the phase it stopped in, above the system's own words. */
function failureWords(t: T, phase: PhaseView | null): { title: string; hint: string } {
  const key: PhaseKey | "default" = phase?.key ?? "default";
  switch (key) {
    case "fetch":
      return { title: t("appPages.deployments.page.failure.fetch.title"), hint: t("appPages.deployments.page.failure.fetch.hint") };
    case "install":
      return { title: t("appPages.deployments.page.failure.install.title"), hint: t("appPages.deployments.page.failure.install.hint") };
    case "build":
      return { title: t("appPages.deployments.page.failure.build.title"), hint: t("appPages.deployments.page.failure.build.hint") };
    case "activate":
      return { title: t("appPages.deployments.page.failure.activate.title"), hint: t("appPages.deployments.page.failure.activate.hint") };
    case "health":
      return { title: t("appPages.deployments.page.failure.health.title"), hint: t("appPages.deployments.page.failure.health.hint") };
    default:
      return { title: t("appPages.deployments.page.failure.default.title"), hint: t("appPages.deployments.page.failure.default.hint") };
  }
}

/** How long it took, or has been running for, kept current while it runs. */
function Elapsed({ deployment, t }: { deployment: Deployment; t: T }) {
  const running = RUNNING.has(deployment.status);
  const now = useNow(() => (running ? 1_000 : 3_600_000));
  const started = parseTimestamp(deployment.started_at);
  if (running) {
    return (
      <>{started === null ? t("appPages.deployments.page.running") : t("appPages.deployments.page.runningFor", { duration: formatDuration(Math.max(0, (now - started.getTime()) / 1000), t.locale) })}</>
    );
  }
  if (deployment.duration_s === null || deployment.duration_s === undefined) return <span className="text-fg-faint">{t("appPages.common.notRecorded")}</span>;
  return <span className="mono text-12">{formatDuration(deployment.duration_s, t.locale)}</span>;
}

function Fact({ term, children }: { term: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1">
      <dt className="text-12 text-fg-faint">{term}</dt>
      <dd className="flex min-w-0 items-center gap-1.5 text-13 text-fg">{children}</dd>
    </div>
  );
}

function Facts({ deployment, t }: { deployment: Deployment; t: T }) {
  const commit = shortCommit(deployment.git_commit);
  const trigger = triggerWords(t, deployment.triggered_by);
  const TriggerIcon = trigger.icon;
  return (
    <dl className="grid grid-cols-2 gap-x-6 gap-y-4 rounded-card border border-border bg-surface px-4 py-3.5 shadow-raised sm:grid-cols-4">
      <Fact term={t("appPages.deployments.fields.commit")}>
        {commit ? (
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex min-w-0 items-center gap-1.5">
              <span translate="no" className="mono text-12">
                {commit}
              </span>
              {deployment.git_branch ? (
                <span translate="no" className="mono truncate text-12 text-fg-muted">
                  {deployment.git_branch}
                </span>
              ) : null}
              <CopyButton value={deployment.git_commit ?? commit} label={t("appPages.deployments.page.copyCommit")} className="-my-1" />
            </div>
            {deployment.commit_message ? (
              <p title={deployment.commit_message} className="truncate text-12 text-fg-muted">
                {deployment.commit_message}
              </p>
            ) : null}
          </div>
        ) : (
          <span className="text-fg-faint">{t("appPages.deployments.page.notGitCheckout")}</span>
        )}
      </Fact>
      <Fact term={t("appPages.deployments.fields.startedBy")}>
        <TriggerIcon aria-hidden="true" className="size-3.5 shrink-0 text-fg-muted" />
        {trigger.label}
      </Fact>
      <Fact term={t("appPages.deployments.fields.started")}>
        <RelativeTime value={deployment.started_at} />
      </Fact>
      <Fact term={t("appPages.deployments.fields.duration")}>
        <Elapsed deployment={deployment} t={t} />
      </Fact>
    </dl>
  );
}

function useBuildLog(deployment: Deployment) {
  const running = RUNNING.has(deployment.status);
  const [tail, setTail] = useState<number | null>(null);
  const log = useQuery({
    ...deploymentLogQuery(deployment.id, tail),
    // The recorder flushes every line as it is written, so re-reading the file streams it.
    refetchInterval: running ? 1_000 : false,
  });
  const lines = useLogLines(log.data?.content);
  return { log, lines, tail, setTail };
}

const LOG_ROW_PX = 20;
const LOG_CHROME_PX = 60;
/** The smaller of the usual frames (26rem): a log taller than this gets the usual frame and scrolls. */
const LOG_FRAME_PX = 416;

function LogSection({
  domain,
  deployment,
  lines,
  log,
  fallback,
  wholeLog,
  t,
}: {
  domain: string;
  deployment: Deployment;
  lines: readonly LogLine[];
  log: ReturnType<typeof useBuildLog>["log"];
  fallback: readonly LogLine[];
  wholeLog: () => void;
  t: T;
}) {
  const running = RUNNING.has(deployment.status);
  const missing = log.data?.missing_reason ?? null;
  const shown = lines.length > 0 ? lines : fallback;
  // Rows of 20px, the toolbar and the padding: a finished log shorter than the usual frame
  // gets a frame its own height (never under 12rem), instead of a well of empty space.
  const natural = shown.length * LOG_ROW_PX + LOG_CHROME_PX;
  const fitted = !running && natural < LOG_FRAME_PX ? Math.max(natural, 192) : null;
  return (
    <Section
      title={t("appPages.deployments.page.buildLogTitle")}
      level={3}
      description={running ? t("appPages.deployments.page.buildLogFollowing") : undefined}
      actions={
        log.data?.truncated ? (
          <Button size="sm" variant="ghost" loading={log.isFetching} onClick={wholeLog}>
            {t("appPages.deployments.page.showWholeLog")}
          </Button>
        ) : undefined
      }
    >
      {log.data?.truncated ? (
        <p className="text-12 text-fg-muted">{t("appPages.deployments.page.logTruncated", { size: formatBytes(DEFAULT_TAIL, t.locale) })}</p>
      ) : null}
      {log.isError && log.data === undefined ? (
        <ErrorBlock error={log.error} title={t("appPages.deployments.page.logLoadError")} onRetry={() => void log.refetch()} retrying={log.isRefetching} />
      ) : log.data === undefined ? (
        <div aria-busy="true" className="h-[26rem] rounded-card border border-border bg-bg-sunken p-4 lg:h-[34rem]">
          <span className="sr-only">{t("appPages.deployments.page.loadingBuildLog")}</span>
          <div aria-hidden="true" className="flex flex-col gap-2.5">
            {["w-2/3", "w-1/2", "w-3/4", "w-2/5", "w-3/5", "w-1/3"].map((width) => (
              <Skeleton key={width} className={`h-3 ${width}`} />
            ))}
          </div>
        </div>
      ) : missing !== null && shown.length === 0 && !running ? (
        <div className="flex flex-col gap-2 rounded-card border border-dashed border-border px-4 py-4">
          <p className="flex items-center gap-2 text-13 font-medium text-fg">
            <FileX aria-hidden="true" className="size-4 text-fg-faint" />
            {t("appPages.deployments.page.noBuildLog")}
          </p>
          <pre className="text-12 whitespace-pre-wrap text-fg-muted">{missing}</pre>
        </div>
      ) : fitted !== null ? (
        // A finished deploy with a short log: the viewer is as tall as what it holds.
        <LogViewer
          lines={shown}
          height={fitted}
          pageSearch
          label={t("appPages.deployments.page.logLabel", { id: String(deployment.id), domain })}
          filename={`${domain}-deployment-${String(deployment.id)}.log`}
          emptyMessage={t("appPages.deployments.page.logEmpty")}
        />
      ) : (
        <div className="h-[26rem] lg:h-[34rem]">
          <LogViewer
            lines={shown}
            height="fill"
            pageSearch
            label={t("appPages.deployments.page.logLabel", { id: String(deployment.id), domain })}
            filename={`${domain}-deployment-${String(deployment.id)}.log`}
            emptyMessage={running ? t("appPages.deployments.page.logWaitingFirstLine") : t("appPages.deployments.page.logEmpty")}
          />
        </div>
      )}
    </Section>
  );
}

function PageSkeleton({ t }: { t: T }) {
  return (
    <div aria-busy="true" className="flex flex-col gap-6">
      <span className="sr-only">{t("appPages.deployments.page.loadingDeployment")}</span>
      <div aria-hidden="true" className="flex flex-col gap-6">
        <Skeleton className="h-6 w-56" />
        <Skeleton className="h-16 w-full rounded-card" />
        <Skeleton className="h-20 w-full rounded-card" />
        <Skeleton className="h-[26rem] w-full rounded-card" />
      </div>
    </div>
  );
}

function Back({ domain, t }: { domain: string; t: T }) {
  return (
    <Link to="/apps/$domain/deployments" params={{ domain }} className={`${LINK} inline-flex items-center gap-1 self-start`}>
      <ChevronLeft aria-hidden="true" className="size-4" />
      {t("appPages.deployments.page.allDeployments")}
    </Link>
  );
}

function Deploy({ domain, deployment, t }: { domain: string; deployment: Deployment; t: T }) {
  const running = RUNNING.has(deployment.status);
  const outcome = outcomeOf(deployment.status);
  const { log, lines, setTail } = useBuildLog(deployment);
  const job = useDeploymentJob(deployment);
  const app = useQuery(appQuery(domain));
  const now = useNow(() => (running ? 1_000 : 3_600_000));
  // A static site is served as files: nothing answers a health check, so it has none.
  const staticSite = app.data !== undefined && !hasUnit(app.data);

  const events = mergeEvents(buildLogEvents(lines), jobEvents(job.entries));
  const span = logSpan([...lines.map((line) => line.at), ...job.entries.map((entry) => entry.at)]);
  const phases = timeline(
    events,
    outcome,
    { lastAt: span.last, now: new Date(now), offset: logClockOffset(span.first, parseTimestamp(deployment.started_at)) },
    { checksHealth: !staticSite },
  );
  const current = currentPhase(phases);

  // Phase changes, and how it ended, are said once each; log lines never.
  const moment = running ? (current?.key ?? "start") : deployment.status;
  const doing = current ? phaseDoing(t, current.key).toLowerCase() : t("appPages.deployments.page.starting");
  const said = running
    ? t("appPages.deployments.page.announceRunning", { id: String(deployment.id), doing })
    : outcome === "failed"
      ? t("appPages.deployments.page.announceFailed", { id: String(deployment.id) })
      : t("appPages.deployments.page.announceFinished", { id: String(deployment.id) });
  useAnnounceChange(moment, said, outcome === "failed" ? "assertive" : "polite");

  const fallback: LogLine[] = job.entries.map((entry, index) => ({
    id: index,
    text: entry.text,
    ...(entry.at ? { ts: logClockText(entry.at) } : {}),
  }));
  const failure = outcome === "failed" ? failureWords(t, current) : null;

  return (
    <div className="flex flex-col gap-6">
      <div className="flex flex-col gap-4">
        <Back domain={domain} t={t} />
        <div className="flex flex-wrap items-start justify-between gap-x-6 gap-y-3">
          <div className="flex min-w-0 flex-wrap items-center gap-3">
            <h2 className="title text-18 text-fg">{t("appPages.deployments.page.heading", { id: String(deployment.id) })}</h2>
            <DeployStatePill status={deployment.status} />
            {job.socket === "reconnecting" ? <StatusPill state="deploying" label={t("appPages.common.reconnecting")} appearance="inline" size="sm" /> : null}
          </div>
          <DeploymentActions domain={domain} deployment={deployment} />
        </div>
      </div>

      <Facts deployment={deployment} t={t} />

      <div className="rounded-card border border-border bg-surface px-3 py-5 shadow-raised sm:px-6">
        <PhaseTimeline phases={phases} outcome={outcome} />
        {staticSite ? (
          <p className="mt-4 border-t border-border pt-3 text-center text-12 text-pretty text-fg-muted">{t("appPages.deployments.page.staticHealthNote")}</p>
        ) : null}
      </div>

      {failure !== null ? (
        <div className="flex flex-col gap-2">
          <ErrorBlock
            error={{ detail: deployment.error ?? t("appPages.deployments.page.noRecordedReason") }}
            title={failure.title}
            hint={failure.hint}
          />
          <Link to="/apps/$domain/diagnose" params={{ domain }} className={`${LINK} self-start`}>
            {t("appPages.deployments.page.diagnoseThisApp")}
          </Link>
        </div>
      ) : null}

      <LogSection
        domain={domain}
        deployment={deployment}
        lines={lines}
        log={log}
        fallback={fallback}
        wholeLog={() => {
          setTail(WHOLE_LOG);
        }}
        t={t}
      />
    </div>
  );
}

/**
 * One deploy: what it built, how far it got phase by phase, its build log streamed while it
 * runs, and if it failed, the error in its own words with what to do about it.
 */
export function DeploymentPage({ domain, id }: { domain: string; id: string }) {
  const t = useT();
  const numeric = /^\d+$/.test(id) ? Number(id) : null;
  useDocumentTitle(t("appPages.deployments.page.documentTitle", { id, domain }), 1);
  const deployment = useQuery({
    ...deploymentQuery(numeric ?? 0),
    enabled: numeric !== null,
    // A deploy from the command line has no job to announce its end; this catches it.
    refetchInterval: (query) => (query.state.data && RUNNING.has(query.state.data.status) ? 3_000 : false),
  });

  const missing = numeric === null || (deployment.isError && isApiError(deployment.error) && deployment.error.status === 404);
  if (missing) {
    return (
      <div className="flex flex-col gap-4">
        <Back domain={domain} t={t} />
        <EmptyState
          level={2}
          icon={<FileX />}
          title={t("appPages.deployments.page.notFoundTitle", { id })}
          description={t("appPages.deployments.page.notFoundDescription")}
          className="py-12"
        />
      </div>
    );
  }
  if (deployment.data === undefined) {
    return deployment.isError ? (
      <ErrorBlock
        error={deployment.error}
        title={t("appPages.deployments.page.loadError", { id })}
        onRetry={() => void deployment.refetch()}
        retrying={deployment.isRefetching}
      />
    ) : (
      <PageSkeleton t={t} />
    );
  }
  if (deployment.data.domain !== domain) {
    const owner = deployment.data.domain;
    return (
      <div className="flex flex-col gap-4">
        <Back domain={domain} t={t} />
        <EmptyState
          level={2}
          icon={<FileX />}
          title={t("appPages.deployments.page.wrongAppTitle", { id, domain })}
          description={t.rich("appPages.deployments.page.wrongAppDescription", {
            owner: (
              <Link to="/apps/$domain/deployments/$id" params={{ domain: owner, id }} className={LINK}>
                {owner}
              </Link>
            ),
          })}
          className="py-12"
        />
      </div>
    );
  }
  return <Deploy domain={domain} deployment={deployment.data} t={t} />;
}
