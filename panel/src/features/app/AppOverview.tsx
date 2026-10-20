import { useQuery } from "@tanstack/react-query";
import { Globe, Webhook } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";

import { appQuery, webhookDeliveriesQuery } from "../../api/queries/apps";
import type { App } from "../../api/queries/apps";
import { certsQuery } from "../../api/queries/certs";
import type { Cert } from "../../api/queries/certs";
import { deploymentsQuery } from "../../api/queries/deployments";
import { appDomainsQuery } from "../../api/queries/domains";
import { previewsQuery } from "../../api/queries/previews";
import type { Preview } from "../../api/queries/previews";
import { useDocumentTitle } from "../../app/documentTitle";
import { DeployStatePill } from "../../components/page/AppStatePill";
import { CommandHint } from "../../components/page/CommandHint";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { ResourceMeter } from "../../components/page/ResourceMeter";
import { Section } from "../../components/page/Section";
import { useNow } from "../../components/page/clock";
import { appStatus } from "../../components/page/status";
import { Card } from "../../components/ui/Card";
import { EmptyState } from "../../components/ui/EmptyState";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { ICONS } from "../../components/ui/icons";
import { Mono } from "../../components/ui/Mono";
import { RowList } from "../../components/ui/RowList";
import { Button } from "../../components/ui/Button";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Skeleton, SkeletonRows, SkeletonSection } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatBytes, formatCount, formatDuration, formatPercent, parseTimestamp } from "../../lib/format";
import type { Deployment } from "../apps/data";
import { appLimits, appReading, useLatestMetrics } from "../apps/data";
import { CERT_WARNING_DAYS } from "../overview/attention";
import { PreviewLogsDrawer } from "./settings/PreviewLogsDrawer";
import { previewName, previewStatus } from "./settings/previews";
import { AdoptedOverview } from "./AdoptedOverview";
import { isAdopted } from "./adopted";
import { RECENT_APP_DEPLOYS } from "./AppBanner";
import { LiveRow } from "./LiveRow";
import { deploymentStatusWord, shortCommit } from "./deployments/words";
import { keptServing, servingDeployment } from "./rollback";
import { findCertificate } from "./lookups";
import { displayAddress } from "./displayAddress";

/** The deployments the overview reads: the store keeps twenty, and the live one may be older than the newest five. */
const HISTORY = 20;

// ---------------------------------------------------------------------------------------
// Live now and the last deployments

/** One line of the history: its state, its name, what changed, when and how long, and what kept serving if it failed. */
function DeploymentLine({ domain, deployment, live, kept, t }: { domain: string; deployment: Deployment; live: boolean; kept: number | null; t: T }) {
  const commit = shortCommit(deployment.git_commit);
  const failed = deployment.status === "failed" && !deployment.cancelled;
  const heading = t("appPages.deployments.page.heading", { id: String(deployment.id) });
  return (
    <RowList.Item>
      <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
        <span className="flex w-28 shrink-0 items-center">
          {live ? <StatusPill state="running" label={t("appPages.deployments.words.live")} appearance="inline" size="sm" /> : <DeployStatePill status={deploymentStatusWord(deployment)} appearance="inline" size="sm" />}
        </span>
        <TextLink variant="row" to="/apps/$domain/deployments/$id" params={{ domain, id: String(deployment.id) }} size="ui" className="flex min-w-0 flex-1 items-baseline gap-2 basis-48">
          <span className="sr-only">{heading}</span>
          <span aria-hidden="true" className="tabular-nums">{`#${String(deployment.id)}`}</span>
          {deployment.commit_message ? (
            <span title={deployment.commit_message} className="min-w-0 truncate font-normal">
              {deployment.commit_message}
            </span>
          ) : null}
        </TextLink>
        {commit !== null ? <Mono tone="muted">{commit}</Mono> : null}
        <RelativeTime value={deployment.finished_at ?? deployment.started_at} className="text-12 text-fg-muted" />
        {deployment.duration_s !== null && deployment.duration_s !== undefined ? (
          <Mono tone="muted">{formatDuration(deployment.duration_s, t.locale)}</Mono>
        ) : null}
        {failed && kept !== null ? <span className="text-12 text-fg-muted">{t("appPages.deployments.words.keptServing", { id: String(kept) })}</span> : null}
      </div>
    </RowList.Item>
  );
}

/** What serves now and the last few deploys, from the history the page reads once. */
function Deploys({ app, t }: { app: App; t: T }) {
  const domain = app.domain;
  const history = useQuery(deploymentsQuery({ domain, limit: HISTORY }));
  if (history.isError && history.data === undefined) {
    return <ErrorBlock error={history.error} title={t("appPages.deployments.tab.loadError")} onRetry={() => void history.refetch()} retrying={history.isRefetching} />;
  }
  if (history.data === undefined) {
    return (
      <LoadingRegion label={t("appPages.overview.loadingDeploys")} className="flex flex-col gap-8">
        <SkeletonSection rows={2} />
        <SkeletonSection rows={RECENT_APP_DEPLOYS} />
      </LoadingRegion>
    );
  }
  const items = history.data.items;
  const serving = servingDeployment(items);
  const recent = items.slice(0, RECENT_APP_DEPLOYS);
  return (
    <>
      <Section title={t("appPages.overview.liveNow")}>
        {serving !== null ? (
          <LiveRow deployment={serving} />
        ) : (
          <EmptyState variant="inline" title={t("appPages.overview.nothingLive")} />
        )}
      </Section>
      <Section
        title={t("appPages.overview.lastDeployments")}
        actions={
          <TextLink to="/apps/$domain/deployments" params={{ domain }} size="ui">
            {t("appPages.overview.viewAll")}
          </TextLink>
        }
      >
        {recent.length === 0 ? (
          <EmptyState variant="inline" title={t("appPages.overview.deploysAppearHere")} />
        ) : (
          <RowList aria-label={t("appPages.overview.deploymentsLabel", { domain })}>
            {recent.map((deployment) => (
              <DeploymentLine key={deployment.id} domain={domain} deployment={deployment} live={serving?.id === deployment.id} kept={keptServing(items, deployment)} t={t} />
            ))}
          </RowList>
        )}
      </Section>
    </>
  );
}

// ---------------------------------------------------------------------------------------
// Health

/** How long the unit has been up, kept current. */
function UpFor({ since, locale }: { since: Date; locale: T["locale"] }) {
  const now = useNow(() => 60_000);
  return <>{formatDuration(Math.max(0, (now - since.getTime()) / 1000), locale)}</>;
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex min-w-0 flex-col gap-1.5">
      <span className="truncate text-13 text-fg-muted">{label}</span>
      <div className="min-w-0 text-13 text-fg">{children}</div>
    </div>
  );
}

function uptimeFact(app: App, t: T): ReactNode {
  const state = appStatus(app.status).state;
  if (state === "static") return t("appPages.overview.alwaysOn");
  if (!app.active) return t("appPages.overview.notRunning");
  const since = parseTimestamp(app.uptime);
  if (since === null) return t("appPages.overview.running");
  return <UpFor since={since} locale={t.locale} />;
}

function certificateFact(cert: Cert | null | undefined, error: unknown, t: T): ReactNode {
  if (cert === undefined && error) return <span className="text-fg-muted">{t("appPages.overview.certificateUnknown")}</span>;
  if (cert === undefined) return <Skeleton className="h-4 w-24" />;
  if (cert === null) return <span className="text-fg-muted">{t("appPages.overview.certificateNone")}</span>;
  const days = cert.days_remaining;
  if (days === null || days === undefined) return t("appPages.overview.certificateIssued");
  const Icon = days < 0 ? ICONS.error : days < CERT_WARNING_DAYS ? ICONS.warning : ICONS.verified;
  const tone = days < 0 ? "text-fail" : days < CERT_WARNING_DAYS ? "text-warn" : "text-ok";
  return (
    <span className="flex items-center gap-1.5">
      <Icon aria-hidden="true" className={cx("size-icon-md shrink-0", tone)} />
      {days < 0 ? t("appPages.overview.certificateExpired") : t("appPages.overview.daysLeft", { count: days })}
    </span>
  );
}

/** CPU and memory now, each against the most the app may use, with the way to change that. */
function useReadings(app: App, t: T) {
  const metrics = useLatestMetrics();
  const reading = appReading(metrics, app.domain);
  const limits = appLimits(app);
  const missing = !app.active
    ? t("appPages.overview.resourceMissingNotRunning")
    : metrics === undefined
      ? t("appPages.overview.resourceMissingWaiting")
      : t("appPages.overview.resourceMissingNoReading");
  return { reading, limits, missing };
}

/**
 * How the app is doing in one row: how long it has been up, what its certificate has left, and
 * its CPU and memory against their limits, with the way to change them. A static site has no
 * process to measure, so its row is the first two.
 */
function Health({ app, t }: { app: App; t: T }) {
  const certs = useQuery(certsQuery());
  const { reading, limits, missing } = useReadings(app, t);
  const staticSite = appStatus(app.status).state === "static";
  // Running now and not enabled at boot: after a restart of the server it stays stopped.
  const service = app.unit ? app.unit.replace(/\.service$/, "") : null;
  const noBoot = !staticSite && app.active && !app.enabled;
  return (
    <Section
      title={t("appPages.overview.healthTitle")}
      actions={
        staticSite ? undefined : (
          <TextLink to="/apps/$domain/settings/resources" params={{ domain: app.domain }} size="ui">
            {t("appPages.overview.editLimits")}
          </TextLink>
        )
      }
    >
      <Card padding="sm">
        <div className={cx("grid grid-cols-2 gap-x-8 gap-y-4", !staticSite && "lg:grid-cols-4")}>
          <Fact label={t("appPages.overview.uptimeLabel")}>{uptimeFact(app, t)}</Fact>
          <Fact label={t("appPages.overview.certificateLabel")}>{certificateFact(findCertificate(certs.data, app.domain), certs.error, t)}</Fact>
          {staticSite ? null : (
            <>
              <ResourceMeter label={t("appPages.common.cpu")} value={reading.cpu} limit={limits.cpu} format={(value) => formatPercent(value, t.locale)} missing={missing} />
              <ResourceMeter label={t("appPages.common.memory")} value={reading.memory} limit={limits.memory} format={(value) => formatBytes(value, t.locale)} missing={missing} />
            </>
          )}
        </div>
        {!staticSite && limits.tasks !== null ? (
          <p className="mt-3 text-12 text-fg-muted">{t("appPages.overview.tasksLimit", { value: formatCount(limits.tasks, t.locale) })}</p>
        ) : null}
        {noBoot ? (
          <InlineMessage
            className="mt-3"
            trailing={
              service !== null ? (
                <TextLink to="/services/$name" params={{ name: service }} size="ui">
                  {t("appPages.overview.openService")}
                </TextLink>
              ) : undefined
            }
          >
            {t("appPages.overview.noBoot")}
          </InlineMessage>
        ) : null}
      </Card>
    </Section>
  );
}

/**
 * CPU and memory of an adopted site's unit, as they have always been drawn for it (its overview
 * is otherwise its own and does not change).
 */
function AdoptedResources({ app, t }: { app: App; t: T }) {
  const { reading, limits, missing } = useReadings(app, t);
  return (
    <Section
      title={t("appPages.overview.resourcesTitle")}
      actions={
        <TextLink to="/apps/$domain/metrics" params={{ domain: app.domain }} size="ui">
          {t("appPages.overview.openMetrics")}
        </TextLink>
      }
    >
      <Card padding="sm">
        <div className="grid gap-x-8 gap-y-4 sm:grid-cols-2">
          <ResourceMeter label={t("appPages.common.cpu")} value={reading.cpu} limit={limits.cpu} format={(value) => formatPercent(value, t.locale)} missing={missing} />
          <ResourceMeter label={t("appPages.common.memory")} value={reading.memory} limit={limits.memory} format={(value) => formatBytes(value, t.locale)} missing={missing} />
        </div>
        {limits.tasks !== null ? <p className="mt-3 text-12 text-fg-muted">{t("appPages.overview.tasksLimit", { value: formatCount(limits.tasks, t.locale) })}</p> : null}
      </Card>
    </Section>
  );
}

// ---------------------------------------------------------------------------------------
// Previews, reach and automation

/** The pull requests and branches that have a preview of their own, when previews are on. */
function Previews({ domain, t }: { domain: string; t: T }) {
  const previews = useQuery(previewsQuery(domain));
  const [logs, setLogs] = useState<Preview | null>(null);
  const data = previews.data;
  // Only an app with previews on has anything to say, and one with none yet says it in its settings.
  if (data === undefined || data.settings === null || data.previews.length === 0) return null;
  return (
    <Section title={t("appPages.overview.previewsTitle")}>
      <RowList aria-label={t("appPages.overview.previewsLabel", { domain })}>
        {data.previews.map((preview) => {
          const view = previewStatus(preview.status, t.locale);
          const name = previewName(preview);
          return (
            <RowList.Item
              key={preview.number}
              actions={
                <>
                  <ExternalLink href={preview.url} label={t("appPages.overview.previewOpenAria", { name })}>
                    {t("appPages.overview.previewOpen")}
                  </ExternalLink>
                  <Button size="sm" variant="ghost" aria-label={t("appPages.overview.previewLogsAria", { name })} onClick={() => setLogs(preview)}>
                    {t("appPages.overview.previewLogs")}
                  </Button>
                </>
              }
            >
              <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1">
                <span className="flex w-28 shrink-0 items-center">
                  <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" />
                </span>
                <Mono>{name}</Mono>
                {preview.kind === "pr" ? (
                  <Mono tone="muted" truncate>
                    {preview.branch}
                  </Mono>
                ) : null}
              </div>
            </RowList.Item>
          );
        })}
      </RowList>
      <PreviewLogsDrawer domain={domain} preview={logs} onClose={() => setLogs(null)} />
    </Section>
  );
}

/** The names the app answers on, and whether a push deploys it: what reaches it and what starts it, in two lines. */
function Reach({ app, t }: { app: App; t: T }) {
  const domain = app.domain;
  const names = useQuery(appDomainsQuery(domain));
  const certs = useQuery(certsQuery());
  const deliveries = useQuery({ ...webhookDeliveriesQuery(domain), enabled: app.webhook_enabled });
  const latest = deliveries.data?.items[0];
  const entries = names.data?.domains ?? [];
  const address = displayAddress(domain, names.data?.domains, certs.data);
  const more = Math.max(0, entries.length - 1);

  let answers: ReactNode;
  if (names.isError && names.data === undefined) {
    answers = <ErrorBlock compact error={names.error} title={t("appPages.overview.domainsLoadError")} />;
  } else if (names.data === undefined) {
    answers = <Skeleton className="h-4 w-64" />;
  } else {
    const named = <Mono>{address.name}</Mono>;
    if (address.secure) answers = more > 0 ? t.rich("appPages.overview.answersSecureMore", { address: named, count: more }) : t.rich("appPages.overview.answersSecure", { address: named });
    else answers = more > 0 ? t.rich("appPages.overview.answersPlainMore", { address: named, count: more }) : t.rich("appPages.overview.answersPlain", { address: named });
  }

  let push: ReactNode;
  if (!app.webhook_enabled) {
    push = t("appPages.overview.pushOffDetail");
  } else if (deliveries.isError && deliveries.data === undefined) {
    push = <ErrorBlock compact error={deliveries.error} title={t("appPages.overview.webhookLoadError")} onRetry={() => void deliveries.refetch()} />;
  } else if (deliveries.isPending) {
    push = <Skeleton className="h-4 w-64" />;
  } else {
    const where = app.branch ? t.rich("appPages.overview.pushBranch", { branch: <Mono>{app.branch}</Mono> }) : t("appPages.overview.pushAnyBranch");
    push =
      latest === undefined ? (
        <>
          {where} {t("appPages.overview.pushWaiting")}
        </>
      ) : (
        <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
          <span>{where}</span>
          <DeployStatePill status={latest.status} appearance="inline" size="sm" />
          {latest.git_commit ? <Mono>{latest.git_commit.slice(0, 7)}</Mono> : null}
          {t.rich("appPages.overview.pushLast", { time: <RelativeTime value={latest.started_at} /> })}
        </span>
      );
  }

  return (
    <RowList aria-label={t("appPages.overview.reachLabel", { domain })}>
      <RowList.Item
        leading={<Globe aria-hidden="true" className="size-icon-md text-fg-muted" />}
        actions={
          <TextLink to="/apps/$domain/domains" params={{ domain }} size="ui">
            {t("appPages.overview.manageDomains")}
          </TextLink>
        }
      >
        <div className="text-13 text-fg">{answers}</div>
      </RowList.Item>
      <RowList.Item
        leading={<Webhook aria-hidden="true" className="size-icon-md text-fg-muted" />}
        actions={
          <TextLink to="/apps/$domain/settings/deploy-on-push" params={{ domain }} size="ui">
            {app.webhook_enabled ? t("appPages.overview.pushSettings") : t("appPages.overview.pushSetUp")}
          </TextLink>
        }
      >
        <div aria-busy={deliveries.isPending && app.webhook_enabled ? true : undefined} className="text-13 text-fg">
          {push}
        </div>
      </RowList.Item>
    </RowList>
  );
}

/**
 * The loaded page's shape: what serves, the last deploys, and the row of health, in the order
 * and at the height they are drawn.
 */
function OverviewSkeleton({ t }: { t: T }) {
  return (
    <LoadingRegion label={t("appPages.overview.loadingApplication")} className="flex flex-col gap-8">
      <SkeletonSection rows={2} />
      <SkeletonSection rows={RECENT_APP_DEPLOYS} />
      <SkeletonSection rows={2} />
      <SkeletonRows rows={2} />
    </LoadingRegion>
  );
}

/**
 * An app at a glance, in the order an operator asks: what serves and what changed, the last
 * deploys, how it is doing, then what reaches it and what starts it. It summarises and links
 * rather than repeating the other tabs, and it says nothing of how the app is configured: that is
 * Settings, General. What is wrong with it is the banner's, above every tab.
 */
export function AppOverview({ domain }: { domain: string }) {
  const t = useT();
  useDocumentTitle(t("appPages.overview.documentTitle", { domain }), 1);
  const app = useQuery(appQuery(domain));

  // The layout owns the load failure and the not-found page; this shows the shape meanwhile.
  if (app.data === undefined) return app.isError ? null : <OverviewSkeleton t={t} />;
  const staticSite = appStatus(app.data.status).state === "static";
  // A site Noust adopted has no deploys, no release and no push to show: its own overview.
  if (isAdopted(app.data)) return <AdoptedOverview app={app.data} resources={staticSite ? null : <AdoptedResources app={app.data} t={t} />} />;

  return (
    <div className="flex flex-col gap-8">
      <Deploys app={app.data} t={t} />
      <Health app={app.data} t={t} />
      <Previews domain={domain} t={t} />
      <Reach app={app.data} t={t} />
      <CommandHint command={`noust status ${domain}`} label={t("appPages.fromTerminal")} />
    </div>
  );
}
