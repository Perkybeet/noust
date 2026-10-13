import { useQuery } from "@tanstack/react-query";
import { Link, Outlet, useMatchRoute } from "@tanstack/react-router";
import { Boxes } from "lucide-react";
import type { ReactNode } from "react";

import { isApiError } from "../../api/client";
import { appQuery, appsQuery } from "../../api/queries/apps";
import type { App } from "../../api/queries/apps";
import { certsQuery } from "../../api/queries/certs";
import { queueQuery } from "../../api/queries/queue";
import { LinkTabs } from "../../app/LinkTabs";
import type { PageHeaderProps } from "../../app/PageHeader";
import { DetailPage } from "../../components/page/DetailPage";
import { JobProgress } from "../../components/page/JobProgress";
import { ErrorBlock } from "../../components/page/QueryState";
import { appStatus } from "../../components/page/status";
import type { StatusView } from "../../components/page/status";
import { useAnnounceChange } from "../../components/page/useAnnounceChange";
import { buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { useTypeName } from "../apps/data";
import { CancelJobButton } from "../queue/CancelJobButton";
import { useCancelJob } from "../queue/useCancelJob";
import { cancelReasonWords, cancelState, mergedRequests, placeOf } from "../queue/words";
import { useAppHeaderActions } from "./AppActions";
import { AppBanner, useAppCondition } from "./AppBanner";
import { PendingBanner, usePendingChanges } from "./PendingChanges";
import { findCertificate } from "./lookups";
import { appTabs } from "./tabs";
import { jobStep, jobWords, useAppJob } from "./useAppJob";
import type { AppJob } from "./useAppJob";

/**
 * Where the app answers: HTTPS unless the certificate list shows none covers it (while the
 * list loads, HTTPS, which is how Noust deploys by default).
 */
function liveUrl(domain: string, hasCertificate: boolean): string {
  return `${hasCertificate ? "https" : "http"}://${domain}`;
}

/** The header's facts line: its type by name, its port, and the live site. */
function Facts({ app, hasCertificate, t }: { app: App; hasCertificate: boolean; t: T }) {
  const typeName = useTypeName();
  const type = typeName(app.app_type);
  return (
    <>
      {type !== null ? <span>{type}</span> : null}
      {app.port ? <span>{t.rich("appPages.layout.portFact", { port: <Mono>{app.port}</Mono> })}</span> : null}
      <ExternalLink href={liveUrl(app.domain, hasCertificate)} label={t("appPages.layout.liveSite", { domain: app.domain })}>
        <span translate="no">{app.domain}</span>
      </ExternalLink>
    </>
  );
}

/** The facts line's shape while the app loads, one line tall like the facts it stands for. */
function FactsSkeleton() {
  return (
    <span aria-hidden="true" className="flex h-5 items-center gap-3">
      <Skeleton className="h-3 w-14" />
      <Skeleton className="h-3 w-16" />
      <Skeleton className="h-3 w-32" />
    </span>
  );
}

/**
 * The job in hand on this app, under the header on every tab: waiting or running with its
 * current step, or how the job this page started failed, in the system's words, until
 * dismissed. A job that waits says its place in the line and what it waits for (the queue's
 * own words), and a job that can be stopped has its Cancel here, where the operator is. Not
 * announced here: the header announces the state it puts the app in, and the job's end is the
 * notice toast's.
 */
function Job({ job, domain, t }: { job: AppJob; domain: string; t: T }) {
  const cancel = useCancelJob();
  const apps = useQuery(appsQuery());
  const queue = useQuery(queueQuery());
  if (job.running) {
    const words = jobWords(job.running.type, domain, t.locale);
    const waiting = job.running.status === "pending";
    // The queue's read of this job is the authority on whether it can be stopped (the job
    // event does not carry that); the job in hand fills in until the queue has answered.
    const running = job.running;
    const entry = [...(queue.data?.running ?? []), ...(queue.data?.queued ?? [])].find((candidate) => candidate.id === running.id) ?? running;
    const state = cancelState(entry, apps.data?.apps);
    const place = placeOf(entry);
    const merged = mergedRequests(entry);
    const reasonId = `job-reason-${entry.id}`;
    const details: { key: string; line: ReactNode }[] = [];
    if (waiting && place !== null) details.push({ key: "place", line: place.next ? t("queue.place.next") : t("queue.place.position", { position: place.position }) });
    if (waiting && entry.waiting_for) details.push({ key: "waiting", line: t.rich("queue.waitingForLine", { what: <Mono key="what">{entry.waiting_for}</Mono> }) });
    if (merged > 0) details.push({ key: "merged", line: t("queue.merged", { count: merged }) });
    if (state.kind === "cannot") details.push({ key: "reason", line: cancelReasonWords(state.reason, t.locale) });
    return (
      <>
        <JobProgress
          announce={false}
          state={waiting ? "queued" : "running"}
          title={words.title}
          step={jobStep(running)}
          {...(details.length > 0
            ? {
                description: (
                  <span id={reasonId} className="flex flex-col gap-0.5">
                    {details.map(({ key, line }) => (
                      <span key={key}>{line}</span>
                    ))}
                  </span>
                ),
              }
            : {})}
          action={<CancelJobButton entry={entry} state={state} onAsk={cancel.ask} reasonId={reasonId} />}
        />
        {cancel.dialog}
      </>
    );
  }
  if (job.failed) {
    const words = jobWords(job.failed.type, domain, t.locale);
    return (
      <JobProgress
        announce={false}
        state="failed"
        title={words.failed}
        error={{ detail: job.failed.error ?? t("appPages.job.noReason") }}
        onDismiss={job.dismiss}
      />
    );
  }
  return null;
}

/** The tabs' and the tab's place while the app is read: replaced, never pushed down. */
function BodySkeleton({ domain, t }: { domain: string; t: T }) {
  return (
    <div aria-busy="true" className="flex flex-col gap-8">
      <span className="sr-only">{t("appPages.layout.loading", { domain })}</span>
      <Skeleton className="h-10 w-full max-w-xl" />
      <Skeleton className="h-64 w-full rounded-card" />
    </div>
  );
}

function NotFound({ domain, header, t }: { domain: string; header: PageHeaderProps; t: T }) {
  return (
    <DetailPage header={header}>
      <EmptyState
        variant="firstUse"
        icon={<Boxes />}
        title={t("appPages.layout.notFoundTitle")}
        description={t("appPages.layout.notFoundDescription", { domain })}
        action={
          <Link to="/apps" className={buttonClassName("secondary")}>
            {t("appPages.layout.allApplications")}
          </Link>
        }
        command="noust list"
      />
    </DetailPage>
  );
}

/**
 * One application, as a T2 page: a header that stays put on every tab (its name, its state,
 * its type, port and live address, Update and the other actions), then what is wrong with it
 * when something is, the job in hand, and its sections as tabs whose state is the URL. The
 * page is one tree from the first frame: the tab under it is never mounted twice as the app's
 * details arrive.
 */
export function AppLayout({ domain }: { domain: string }) {
  const t = useT();
  const { node } = useNode();
  const app = useQuery(appQuery(domain));
  const certs = useQuery(certsQuery());
  const job = useAppJob(domain);
  const condition = useAppCondition(domain, app.data);
  const pending = usePendingChanges(domain);
  const matchRoute = useMatchRoute();
  const onDiagnose = matchRoute({ to: "/apps/$domain/diagnose", params: { domain } }) !== false;
  const { dialogs, ...actions } = useAppHeaderActions(domain, app.data, { busy: job.running !== null, onJobQueued: job.track });

  const base = appStatus(app.data?.status, t.locale);
  // A job running on the app is its state, whatever systemd says about the unit meanwhile.
  const view: StatusView = job.running
    ? { state: "deploying", label: jobWords(job.running.type, domain, t.locale).running, attention: false }
    : base;

  useAnnounceChange(app.data ? view.label : null, `${domain}: ${view.label}`, view.state === "failed" ? "assertive" : "polite");

  const header: PageHeaderProps = {
    title: domain,
    mono: true,
    server: node,
    breadcrumbs: [{ label: t("nav.apps.label"), to: "/apps" }],
  };

  if (app.isError && isApiError(app.error) && app.error.status === 404) return <NotFound domain={domain} header={header} t={t} />;

  const loaded = app.data;
  const failedToLoad = loaded === undefined && app.isError;
  // The tabs and the tab under them wait for what decides whether a banner goes above them, so
  // nothing the operator is looking at moves down when one arrives. The tab is mounted (and
  // reading) from the first frame, only not shown.
  const ready = condition.known || failedToLoad;
  // While the app is read, the tabs' and the tab's shapes stand in the banner's slot: whatever
  // comes next (a banner or none, then the tabs) takes their place, and the content under them,
  // hidden and so without a box of its own until then, has nothing to move.
  let banner: ReactNode = ready ? null : <BodySkeleton domain={domain} t={t} />;
  if (failedToLoad) {
    banner = <ErrorBlock error={app.error} title={t("appPages.layout.loadError", { domain })} onRetry={() => void app.refetch()} retrying={app.isRefetching} />;
  } else if (ready && job.running === null && condition.condition !== null) {
    banner = <AppBanner domain={domain} condition={condition.condition} onDiagnose={onDiagnose} />;
  } else if (ready && job.running === null && pending.data?.pending === true) {
    // Nothing is wrong with the app: it is set to something it does not run with yet.
    banner = <PendingBanner domain={domain} pending={pending.data} />;
  }

  return (
    <>
      <DetailPage
        header={{
          ...header,
          status: loaded ? <StatusPill state={view.state} label={view.label} /> : <Skeleton className="h-6 w-24 rounded-pill" />,
          meta: loaded ? <Facts app={loaded} hasCertificate={findCertificate(certs.data, domain) !== null} t={t} /> : <FactsSkeleton />,
          ...actions,
        }}
        {...(banner !== null ? { banner } : {})}
        {...(job.running !== null || job.failed !== null ? { job: <Job job={job} domain={domain} t={t} /> } : {})}
        {...(ready ? { tabs: <LinkTabs label={t("nav.landmarks.appSections")} tabs={appTabs(domain)} /> } : {})}
      >
        <div hidden={!ready}>
          <Outlet />
        </div>
      </DetailPage>
      {dialogs}
    </>
  );
}
