import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { FileX, Rocket, ScrollText, Webhook } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { isApiError } from "../../../api/client";
import { appQuery } from "../../../api/queries/apps";
import { certsQuery } from "../../../api/queries/certs";
import { deploymentLogQuery, deploymentQuery, deploymentsQuery } from "../../../api/queries/deployments";
import type { Deployment } from "../../../api/queries/deployments";
import { appDomainsQuery } from "../../../api/queries/domains";
import { rolloutQuery } from "../../../api/queries/rollout";
import { useDocumentTitle } from "../../../app/documentTitle";
import { DeployStatePill } from "../../../components/page/AppStatePill";
import { useNow } from "../../../components/page/clock";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Section } from "../../../components/page/Section";
import { useAnnounceChange } from "../../../components/page/useAnnounceChange";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { CopyButton } from "../../../components/ui/CopyButton";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { ExternalLink } from "../../../components/ui/ExternalLink";
import { LogViewer } from "../../../components/ui/LogViewer";
import type { LogLine } from "../../../components/ui/LogViewer";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { StatusGlyph, StatusPill } from "../../../components/ui/StatusPill";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes, formatDuration, parseTimestamp } from "../../../lib/format";
import { hasUnit } from "../../apps/AppRowActions";
import { displayAddress } from "../displayAddress";
import { keptServing, servingDeployment } from "../rollback";
import { buildLogEvents, excerptText, failureExcerpt, jobEvents, logClockText, logSpan, mergeEvents, useLogLines } from "./buildLog";
import { useDeploymentActions } from "./DeploymentActions";
import { PhaseTimeline, phaseDoing } from "./PhaseTimeline";
import { ShareLog } from "./ShareLog";
import { currentPhase, logClockOffset, outcomeOf, timeline } from "./phases";
import type { PhaseKey, PhaseView } from "./phases";
import { useDeploymentJob } from "./useDeploymentJob";
import { deploymentStatusWord, shortCommit, triggerWords } from "./words";

const RUNNING = new Set(["queued", "running"]);

/** Bytes of the captured log read by default: the backend's own default tail. */
const DEFAULT_TAIL = 512 * 1024;
/** Bytes asked for when the operator wants the whole log. */
const WHOLE_LOG = 64 * 1024 * 1024;

/** The id the build log's section is reached by from "Show it in the log". */
const LOG_ANCHOR = "build-log";

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
  if (deployment.duration_s === null || deployment.duration_s === undefined) return <EmptyCell reason={t("appPages.common.notRecorded")} />;
  return <Mono>{formatDuration(deployment.duration_s, t.locale)}</Mono>;
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
    <dl className="grid grid-cols-2 gap-x-6 gap-y-4 sm:grid-cols-4">
      <Fact term={t("appPages.deployments.fields.commit")}>
        {commit ? (
          <div className="flex min-w-0 flex-col gap-1">
            <div className="flex min-w-0 items-center gap-1.5">
              <Mono>{commit}</Mono>
              {deployment.git_branch ? (
                <Mono tone="muted" truncate>
                  {deployment.git_branch}
                </Mono>
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
        <TriggerIcon aria-hidden="true" className="size-icon-sm shrink-0 text-fg-muted" />
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

type HookRun = NonNullable<Deployment["hooks"]>[number];

/** How one hook ended, as a glyph and a word. */
function hookOutcome(t: T, hook: HookRun): { state: "running" | "failed" | "unknown"; word: string } {
  if (hook.timed_out) return { state: "failed", word: t("appPages.deployments.hooks.timedOut") };
  if (hook.exit_code === null || hook.exit_code === undefined) return { state: "unknown", word: t("appPages.deployments.hooks.didNotRun") };
  if (hook.ok) return { state: "running", word: t("appPages.deployments.hooks.succeeded") };
  return { state: "failed", word: t("appPages.deployments.hooks.exited", { code: hook.exit_code }) };
}

/**
 * What the deploy hooks ran, in order (spec 3.2, section 1.1): before the new version served
 * and after it did, each with how it ended and the end of its output verbatim; and whether the
 * deploy changed the database's schema, which going back past it asks about.
 */
function HooksSection({ deployment, t }: { deployment: Deployment; t: T }) {
  const hooks = deployment.hooks ?? [];
  if (hooks.length === 0 && !deployment.schema_changed) return null;
  return (
    <Section
      title={t("appPages.deployments.hooks.title")}
      icon={<Webhook />}
      level={3}
      description={deployment.schema_changed ? t("appPages.deployments.hooks.schemaChanged") : t("appPages.deployments.hooks.schemaUnchanged")}
    >
      {hooks.length > 0 ? (
        <Card padding="none">
          <ul aria-label={t("appPages.deployments.hooks.listLabel", { id: String(deployment.id) })} className="flex flex-col divide-y divide-border">
            {hooks.map((hook, index) => {
              const outcome = hookOutcome(t, hook);
              return (
                <li key={`${hook.phase}-${String(index)}`} className="flex min-w-0 flex-col gap-2 px-4 py-3">
                  <div className="flex min-w-0 flex-wrap items-center justify-between gap-x-3 gap-y-1">
                    <span className="flex min-w-0 flex-wrap items-baseline gap-x-2 text-13">
                      <span className="text-fg-muted">
                        {hook.phase === "post_deploy" ? t("appPages.deployments.hooks.postDeploy") : t("appPages.deployments.hooks.preDeploy")}
                      </span>
                      <Mono truncate title={hook.run}>
                        {hook.run}
                      </Mono>
                    </span>
                    <span className="flex shrink-0 items-center gap-1.5 text-12 text-fg-muted">
                      <StatusGlyph state={outcome.state} />
                      {outcome.word}
                      {hook.duration_s !== null && hook.duration_s !== undefined ? <Mono tone="muted">{formatDuration(hook.duration_s, t.locale)}</Mono> : null}
                    </span>
                  </div>
                  <span className="flex flex-wrap gap-x-3 text-12 text-fg-muted">
                    {hook.automatic === "prisma" ? <span>{t("appPages.deployments.hooks.prismaAutomatic")}</span> : null}
                    {hook.service ? <span>{t("appPages.deployments.hooks.inService", { service: hook.service })}</span> : null}
                    {hook.migrates ? <span>{t("appPages.deployments.hooks.migrates")}</span> : null}
                  </span>
                  {hook.output !== "" ? <SystemOutput label={t("appPages.deployments.hooks.outputLabel", { run: hook.run })}>{hook.output}</SystemOutput> : null}
                </li>
              );
            })}
          </ul>
        </Card>
      ) : null}
    </Section>
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
/** The smallest the usual frame gets (24rem, `h-editor`): a log taller than this gets it and scrolls. */
const LOG_FRAME_PX = 384;

function LogSection({
  domain,
  deployment,
  lines,
  log,
  fallback,
  wholeLog,
  highlightId,
  t,
}: {
  domain: string;
  deployment: Deployment;
  lines: readonly LogLine[];
  log: ReturnType<typeof useBuildLog>["log"];
  fallback: readonly LogLine[];
  wholeLog: () => void;
  /** The line a failure is about: the viewer opens on it. */
  highlightId: number | undefined;
  t: T;
}) {
  const running = RUNNING.has(deployment.status);
  const missing = log.data?.missing_reason ?? null;
  // The level and the times are the viewer's chips and toggle: this holds every line.
  const shown = lines.length > 0 ? lines : fallback;
  // Rows of 20px, the toolbar and the padding: a finished log shorter than the usual frame
  // gets a frame its own height (never under 12rem), instead of a well of empty space.
  const natural = shown.length * LOG_ROW_PX + LOG_CHROME_PX;
  const fitted = !running && natural < LOG_FRAME_PX ? Math.max(natural, 192) : null;
  const highlight = highlightId !== undefined ? { highlightId } : {};
  return (
    <div id={LOG_ANCHOR} className="scroll-mt-20">
      <Section
        title={t("appPages.deployments.page.buildLogTitle")}
        icon={<ScrollText />}
        level={3}
        description={running ? t("appPages.deployments.page.buildLogFollowing") : undefined}
        actions={
          log.data?.truncated ? (
            <Button size="sm" variant="ghost" icon={<ScrollText aria-hidden="true" />} loading={log.isFetching} onClick={wholeLog}>
              {t("appPages.deployments.page.showWholeLog")}
            </Button>
          ) : undefined
        }
      >
        {log.data === undefined && !log.isError ? (
          // The button's place, held while the log is read: drawn when the log came, it pushed
          // the viewer down by its own height.
          <div aria-hidden="true">
            <Skeleton className="h-control-sm w-32 rounded-control" />
          </div>
        ) : log.data !== undefined && (shown.length > 0 || running) ? (
          <ShareLog deploymentId={deployment.id} disabled={shown.length === 0} />
        ) : null}
        {log.data?.truncated ? (
          <p className="text-12 text-fg-muted">{t("appPages.deployments.page.logTruncated", { size: formatBytes(DEFAULT_TAIL, t.locale) })}</p>
        ) : null}
        {log.isError && log.data === undefined ? (
          <ErrorBlock error={log.error} title={t("appPages.deployments.page.logLoadError")} onRetry={() => void log.refetch()} retrying={log.isRefetching} />
        ) : log.data === undefined ? (
          <LoadingRegion label={t("appPages.deployments.page.loadingBuildLog")}>
            <div className="h-editor rounded-card border border-border bg-bg-sunken p-4">
              <div aria-hidden="true" className="flex flex-col gap-2.5">
                {["w-2/3", "w-1/2", "w-3/4", "w-2/5", "w-3/5", "w-1/3"].map((width) => (
                  <Skeleton key={width} className={`h-3 ${width}`} />
                ))}
              </div>
            </div>
          </LoadingRegion>
        ) : missing !== null && shown.length === 0 && !running ? (
          <Notice title={t("appPages.deployments.page.noBuildLog")}>
            <SystemOutput label={t("appPages.deployments.page.noBuildLogWhy")}>{missing}</SystemOutput>
          </Notice>
        ) : fitted !== null ? (
          // A finished deploy with a short log: the viewer is as tall as what it holds.
          <LogViewer
            lines={shown}
            height={fitted}
            pageSearch
            {...highlight}
            label={t("appPages.deployments.page.logLabel", { id: String(deployment.id), domain })}
            filename={`${domain}-deployment-${String(deployment.id)}.log`}
            emptyMessage={t("appPages.deployments.page.logEmpty")}
          />
        ) : (
          <div className="h-editor">
            <LogViewer
              lines={shown}
              height="fill"
              pageSearch
              {...highlight}
              label={t("appPages.deployments.page.logLabel", { id: String(deployment.id), domain })}
              filename={`${domain}-deployment-${String(deployment.id)}.log`}
              emptyMessage={running ? t("appPages.deployments.page.logWaitingFirstLine") : t("appPages.deployments.page.logEmpty")}
            />
          </div>
        )}
      </Section>
    </div>
  );
}

function PageSkeleton({ t }: { t: T }) {
  return (
    <LoadingRegion label={t("appPages.deployments.page.loadingDeployment")} className="flex flex-col gap-6">
      <Skeleton className="h-6 w-56" />
      <Skeleton className="h-16 w-full rounded-card" />
      <Skeleton className="h-20 w-full rounded-card" />
      <Skeleton className="h-editor w-full rounded-card" />
    </LoadingRegion>
  );
}

/**
 * Which version is serving while this deployment is not: the one that kept serving through its
 * failure, or the one the app went back to when the startup check refused it. The first deploy of
 * an app has nothing behind it, and says so.
 */
function ServingLine({ domain, deployment, phase, t }: { domain: string; deployment: Deployment; phase: PhaseView | null; t: T }) {
  const history = useQuery(deploymentsQuery({ domain, limit: 20 }));
  const names = useQuery(appDomainsQuery(domain));
  const certs = useQuery(certsQuery());
  if (history.data === undefined) return <Skeleton className="h-4 w-72" />;
  const items = history.data.items;
  const kept = keptServing(items, deployment);
  const serving = servingDeployment(items);
  const address = displayAddress(domain, names.data?.domains, certs.data);
  let words: string;
  if (kept === null) words = t("appPages.deployments.page.servingNothing");
  else if (phase !== null && (phase.key === "activate" || phase.key === "health")) words = t("appPages.deployments.page.servingWentBack", { id: String(kept) });
  else if (serving !== null && serving.id !== kept) words = t("appPages.deployments.page.servingKept", { id: String(kept) });
  else words = t("appPages.deployments.page.servingStill", { id: String(kept), address: address.name });
  return (
    <p className="flex items-center gap-2 text-13 text-fg">
      <StatusGlyph state={kept === null ? "stopped" : "running"} size={12} className={kept === null ? "text-idle" : "text-ok"} />
      {words}
    </p>
  );
}

/**
 * What a failed deploy says first, in the order an operator needs it: which version is serving
 * meanwhile, what stopped it and what to do about it (the fix above, the system's words below,
 * verbatim), where it first went wrong and how the phase ended, and the ways on: the log at that
 * line, Diagnose, and the environment when the phase read it.
 */
function FailureBlock({
  domain,
  deployment,
  phase,
  excerpt,
  t,
}: {
  domain: string;
  deployment: Deployment;
  phase: PhaseView | null;
  excerpt: ReturnType<typeof failureExcerpt>;
  t: T;
}) {
  const words = failureWords(t, phase);
  const readsEnvironment = phase !== null && (phase.key === "install" || phase.key === "build");
  const text = excerptText(excerpt, deployment.error);
  const tail = text.output;
  const detail = text.detail ?? t("appPages.deployments.page.noRecordedReason");
  return (
    <div className="flex flex-col gap-3">
      <ServingLine domain={domain} deployment={deployment} phase={phase} t={t} />
      <ErrorBlock
        error={{ detail, ...(tail !== "" ? { output: tail } : {}) }}
        title={words.title}
        hint={words.hint}
        action={
          <>
            <a href={`#${LOG_ANCHOR}`} className={buttonClassName("secondary", "sm")}>
              {t("appPages.deployments.page.showInLog")}
            </a>
            <Link to="/apps/$domain/diagnose" params={{ domain }} className={buttonClassName("secondary", "sm")}>
              {t("appPages.deployments.page.openDiagnose")}
            </Link>
            {readsEnvironment ? (
              <Link to="/apps/$domain/environment" params={{ domain }} className={buttonClassName("secondary", "sm")}>
                {t("appPages.deployments.page.editEnvironment")}
              </Link>
            ) : null}
          </>
        }
      />
    </div>
  );
}

/**
 * The row a deploy that serves opens with: it is live, at which address, and whether HTTPS
 * covers it. The first deploy of an app adds the three things worth doing next. It states a fact,
 * so it is a row and not a banner, and it is green only where the app is serving.
 */
function ArrivalRow({ domain, first, t }: { domain: string; first: boolean; t: T }) {
  const names = useQuery(appDomainsQuery(domain));
  const certs = useQuery(certsQuery());
  const address = displayAddress(domain, names.data?.domains, certs.data);
  const url = `${address.secure ? "https" : "http"}://${address.name}`;
  return (
    <Card padding="sm">
      <div className="flex min-w-0 flex-col gap-2">
        <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-2">
          <StatusPill state="running" label={t("appPages.deployments.words.live")} />
          <ExternalLink href={url} label={t("appPages.layout.liveSite", { domain: address.name })}>
            <Mono>{url}</Mono>
          </ExternalLink>
          <CopyButton value={url} label={t("appPages.deployments.page.copyAddress")} />
        </div>
        <p className="text-13 text-fg-muted">
          {address.secure ? (
            t("appPages.deployments.page.httpsReady")
          ) : (
            <>
              {t("appPages.deployments.page.httpOnly")}{" "}
              <TextLink to="/apps/$domain/domains" params={{ domain }} size="ui">
                {t("appPages.deployments.page.openDomains")}
              </TextLink>
            </>
          )}
        </p>
        {first ? (
          <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-13 text-fg-muted">
            <span>{t("appPages.deployments.page.next")}</span>
            <TextLink to="/apps/$domain/domains" params={{ domain }} size="ui">
              {t("appPages.deployments.page.nextDomain")}
            </TextLink>
            <TextLink to="/apps/$domain/database" params={{ domain }} size="ui">
              {t("appPages.deployments.page.nextDatabase")}
            </TextLink>
            <TextLink to="/backups" size="ui">
              {t("appPages.deployments.page.nextBackup")}
            </TextLink>
          </p>
        ) : null}
      </div>
    </Card>
  );
}

/** Whether this is the first deploy the history has seen: nothing older, and nothing pruned. */
function useIsFirst(domain: string, deployment: Deployment): boolean {
  const history = useQuery(deploymentsQuery({ domain, limit: 20 }));
  const rollout = useQuery({ ...rolloutQuery(domain), retry: false });
  if (history.data === undefined) return false;
  const keep = rollout.data?.history_keep_effective ?? 20;
  const ids = history.data.items.map((item) => item.id);
  return history.data.items.length < keep && ids.every((id) => id >= deployment.id);
}

function Deploy({ domain, deployment, t }: { domain: string; deployment: Deployment; t: T }) {
  const running = RUNNING.has(deployment.status);
  const outcome = outcomeOf(deployment.status);
  const { log, lines, setTail } = useBuildLog(deployment);
  const job = useDeploymentJob(deployment);
  const actions = useDeploymentActions(domain, deployment);
  const app = useQuery(appQuery(domain));
  const history = useQuery(deploymentsQuery({ domain, limit: 20 }));
  const first = useIsFirst(domain, deployment);
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
  // Cancelled by an operator: stored as failed, but nothing went wrong, so it is neither
  // announced as a failure nor shown with an error.
  const cancelled = deployment.cancelled;
  const said = running
    ? t("appPages.deployments.page.announceRunning", { id: String(deployment.id), doing })
    : cancelled
      ? t("queue.cancel.announced", { id: String(deployment.id) })
      : outcome === "failed"
        ? t("appPages.deployments.page.announceFailed", { id: String(deployment.id) })
        : t("appPages.deployments.page.announceFinished", { id: String(deployment.id) });
  useAnnounceChange(moment, said, outcome === "failed" && !cancelled ? "assertive" : "polite");

  const fallback: LogLine[] = job.entries.map((entry, index) => ({
    id: index,
    text: entry.text,
    ...(entry.at ? { ts: logClockText(entry.at) } : {}),
  }));
  const failed = outcome === "failed" && !cancelled;
  // Where it first went wrong, and how the phase ended: read once the log is in.
  const excerpt = failed ? failureExcerpt(lines, current?.key ?? null) : null;
  // The version that serves is the newest one that succeeded: this page's deploy is it, or not.
  const serving = history.data === undefined ? null : servingDeployment(history.data.items);
  const live = !running && !failed && !cancelled && serving?.id === deployment.id;

  return (
    <div className="flex flex-col gap-4">
      <Section
        title={t("appPages.deployments.page.heading", { id: String(deployment.id) })}
        icon={<Rocket />}
        badge={
          <span className="flex items-center gap-2">
            <DeployStatePill status={deploymentStatusWord(deployment)} />
            {job.socket === "reconnecting" ? <StatusPill state="deploying" label={t("appPages.common.reconnecting")} appearance="inline" size="sm" /> : null}
          </span>
        }
        {...(actions.buttons !== null ? { actions: actions.buttons } : {})}
      >
        {actions.outcome}
        {failed && excerpt !== null ? <FailureBlock domain={domain} deployment={deployment} phase={current} excerpt={excerpt} t={t} /> : null}
        {live ? <ArrivalRow domain={domain} first={first} t={t} /> : null}
        <Card padding="none">
          <div className="px-4 py-3.5">
            <Facts deployment={deployment} t={t} />
          </div>
          <div className="border-t border-border px-3 py-5 sm:px-6">
            <PhaseTimeline phases={phases} outcome={outcome} />
            {staticSite ? (
              <p className="mt-4 border-t border-border pt-3 text-center text-12 text-pretty text-fg-muted">{t("appPages.deployments.page.staticHealthNote")}</p>
            ) : null}
          </div>
        </Card>
        {cancelled ? (
          <Notice title={t("queue.cancel.deploymentCancelledTitle")}>
            {deployment.cancelled_by
              ? t("queue.cancel.deploymentCancelled", { who: deployment.cancelled_by })
              : t("queue.cancel.deploymentCancelledAnonymous")}
          </Notice>
        ) : null}
        {deployment.warnings && !failed && !cancelled ? (
          <Notice tone="warning" title={t("appPages.deployments.hooks.warningsTitle")}>
            <div className="flex flex-col gap-2">
              <p>{t("appPages.deployments.hooks.warningsBody")}</p>
              <SystemOutput label={t("appPages.deployments.hooks.warningsLabel")}>{deployment.warnings}</SystemOutput>
            </div>
          </Notice>
        ) : null}
      </Section>

      <HooksSection deployment={deployment} t={t} />
      <LogSection
        domain={domain}
        deployment={deployment}
        lines={lines}
        log={log}
        fallback={fallback}
        wholeLog={() => {
          setTail(WHOLE_LOG);
        }}
        highlightId={excerpt?.firstError?.id}
        t={t}
      />
      {actions.dialogs}
    </div>
  );
}

/**
 * One deploy: what it built, how far it got phase by phase, its build log streamed while it
 * runs, and if it failed, which version still serves, the error in its own words with what to do
 * about it, and the line where it first went wrong. Where this page sits is the header's
 * breadcrumb, not a link of its own.
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
  // Whether the app is a static site decides which phases the timeline has and whether a note
  // sits under it, so the page waits for the answer (the header's own read, usually in already)
  // instead of drawing the timeline and then rearranging it under the log.
  const app = useQuery(appQuery(domain));

  const missing = numeric === null || (deployment.isError && isApiError(deployment.error) && deployment.error.status === 404);
  if (missing) {
    return (
      <EmptyState
        variant="firstUse"
        level={2}
        icon={<FileX />}
        title={t("appPages.deployments.page.notFoundTitle", { id })}
        description={t("appPages.deployments.page.notFoundDescription")}
      />
    );
  }
  if (deployment.data === undefined || (app.isPending && !deployment.isError)) {
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
      <EmptyState
        variant="firstUse"
        level={2}
        icon={<FileX />}
        title={t("appPages.deployments.page.wrongAppTitle", { id, domain })}
        description={t.rich("appPages.deployments.page.wrongAppDescription", {
          owner: (
            <TextLink to="/apps/$domain/deployments/$id" params={{ domain: owner, id }} size="ui">
              {owner}
            </TextLink>
          ),
        })}
      />
    );
  }
  return <Deploy domain={domain} deployment={deployment.data} t={t} />;
}
