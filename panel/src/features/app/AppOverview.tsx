import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { CircleCheck, TriangleAlert, Webhook, X } from "lucide-react";
import type { ReactNode } from "react";

import { appQuery, releasesQuery, webhookDeliveriesQuery } from "../../api/queries/apps";
import type { App } from "../../api/queries/apps";
import { certsQuery } from "../../api/queries/certs";
import { appDomainsQuery } from "../../api/queries/domains";
import type { Cert } from "../../api/queries/certs";
import { deploymentsQuery } from "../../api/queries/deployments";
import type { SiteEntry } from "../../api/queries/sites";
import { sitesQuery } from "../../api/queries/sites";
import { useDocumentTitle } from "../../app/documentTitle";
import { DeployStatePill } from "../../components/page/AppStatePill";
import { CommandHint } from "../../components/page/CommandHint";
import { KeyValueList, KeyValueListSkeleton } from "../../components/page/KeyValueList";
import type { KeyValueItem } from "../../components/page/KeyValueList";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { ResourceMeter } from "../../components/page/ResourceMeter";
import { Section } from "../../components/page/Section";
import { StatTile } from "../../components/page/StatTile";
import { useNow } from "../../components/page/clock";
import { appStatus, deployStatus } from "../../components/page/status";
import { Badge } from "../../components/ui/Badge";
import { Skeleton } from "../../components/ui/Skeleton";
import { STATUS, StatusGlyph } from "../../components/ui/StatusPill";
import { Tooltip } from "../../components/ui/Tooltip";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatBytes, formatCount, formatDateTime, formatDuration, formatPercent, parseTimestamp } from "../../lib/format";
import type { Deployment } from "../apps/data";
import { appLimits, appReading, deployMoment, useLatestMetrics } from "../apps/data";
import { CERT_WARNING_DAYS } from "../overview/attention";
import { CertificateStatus } from "../domains/CertificateStatus";
import { coverageOf, covers } from "../domains/certificates";
import { findCertificate, findSite } from "./lookups";
import { sourceLink } from "./SourceLink";

const TONE_TEXT = { ok: "text-ok", warn: "text-warn", fail: "text-fail", idle: "text-idle" } as const;
const LINK =
  "rounded-[4px] text-13 font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus";

/** How many deploys the overview draws as dots; the rest are on the Deployments tab. */
const DOTS = 5;

/** A surface for a section's content. */
function Panel({ children, className }: { children: ReactNode; className?: string }) {
  return <div className={cx("min-w-0 rounded-card border border-border bg-surface px-4 py-2 shadow-raised", className)}>{children}</div>;
}

// ---------------------------------------------------------------------------------------
// The tiles

/** The last deploys as state dots, oldest first, each opening its deployment. */
function DeployDots({ domain, deploys, t }: { domain: string; deploys: readonly Deployment[]; t: T }) {
  const ordered = [...deploys].reverse();
  return (
    <ol aria-label={t("appPages.overview.lastDeploys", { count: ordered.length })} className="-ml-1 flex items-center gap-0.5">
      {ordered.map((deploy) => {
        const view = deployStatus(deploy.status, t.locale);
        const moment = parseTimestamp(deployMoment(deploy));
        const when = moment === null ? "" : `, ${formatDateTime(moment, t.locale)}`;
        const commit = deploy.git_commit ? ` ${deploy.git_commit.slice(0, 7)}` : "";
        const details = `${view.label}${commit}${when}`;
        return (
          <li key={deploy.id}>
            <Tooltip content={details}>
              <Link
                to="/apps/$domain/deployments/$id"
                params={{ domain, id: String(deploy.id) }}
                aria-label={t("appPages.overview.deployAriaLabel", { id: String(deploy.id), details })}
                className={cx(
                  "flex size-7 items-center justify-center rounded-pill hover:bg-surface-hover focus-visible:outline-2 focus-visible:outline-focus",
                  TONE_TEXT[STATUS[view.state].tone],
                )}
              >
                <StatusGlyph state={view.state} size={14} />
              </Link>
            </Tooltip>
          </li>
        );
      })}
    </ol>
  );
}

/** How long the unit has been up, kept current. */
function UpFor({ since, locale }: { since: Date; locale: T["locale"] }) {
  const now = useNow(() => 60_000);
  return <>{formatDuration(Math.max(0, (now - since.getTime()) / 1000), locale)}</>;
}

function UptimeTile({ app, t }: { app: App; t: T }) {
  const label = t("appPages.overview.uptimeLabel");
  const state = appStatus(app.status).state;
  if (state === "static") return <StatTile label={label} value={t("appPages.overview.alwaysOn")} detail={t("appPages.overview.servedNoProcess")} />;
  if (!app.active) {
    return (
      <StatTile
        label={label}
        value={t("appPages.overview.notRunning")}
        detail={app.enabled ? t("appPages.common.startsAtBoot") : t("appPages.overview.doesNotStartAtBoot")}
      />
    );
  }
  const since = parseTimestamp(app.uptime);
  if (since === null) return <StatTile label={label} value={t("appPages.overview.running")} detail={app.uptime ?? t("appPages.overview.startTimeNotReported")} />;
  return (
    <StatTile
      label={label}
      value={
        <span className="title text-18 text-fg">
          <UpFor since={since} locale={t.locale} />
        </span>
      }
      detail={t.rich("appPages.overview.started", { time: <RelativeTime value={since} /> })}
    />
  );
}

function CertificateTile({ cert, error, t }: { cert: Cert | null | undefined; error: unknown; t: T }) {
  const label = t("appPages.overview.certificateLabel");
  if (cert === undefined && error) return <StatTile label={label} value={t("appPages.overview.certificateUnknown")} detail={t("appPages.overview.certificateCouldNotList")} />;
  if (cert === undefined) return <StatTile label={label} value={<Skeleton className="h-5 w-24" />} />;
  if (cert === null) return <StatTile label={label} value={t("appPages.overview.certificateNone")} detail={t("appPages.overview.certificateNoneCovers")} />;
  const days = cert.days_remaining;
  if (days === null || days === undefined) return <StatTile label={label} value={t("appPages.overview.certificateIssued")} detail={cert.valid_until ?? undefined} />;
  const icon =
    days < 0 ? (
      <X aria-hidden="true" className="size-4 text-fail" />
    ) : days < CERT_WARNING_DAYS ? (
      <TriangleAlert aria-hidden="true" className="size-4 text-warn" />
    ) : (
      <CircleCheck aria-hidden="true" className="size-4 text-ok" />
    );
  const text = days < 0 ? t("appPages.overview.certificateExpired") : t("appPages.overview.daysLeft", { count: days });
  return (
    <StatTile
      label={label}
      value={
        <span className="flex items-center gap-2">
          {icon}
          <span className="title text-18 text-fg">{text}</span>
        </span>
      }
      detail={cert.expires_on ? t("appPages.overview.validUntil", { date: cert.expires_on }) : undefined}
    />
  );
}

function Tiles({ app, t }: { app: App; t: T }) {
  const domain = app.domain;
  const deploys = useQuery(deploymentsQuery({ domain, limit: DOTS }));
  const releases = useQuery({ ...releasesQuery(domain), enabled: app.layout === "releases" });
  const certs = useQuery(certsQuery());

  const items = deploys.data?.items ?? [];
  const newest = items[0];
  const lastGood = items.find((deploy) => deploy.status === "success");
  const active = releases.data?.items.find((release) => release.active);

  let current: ReactNode;
  if (active) {
    current = (
      <StatTile
        label={t("appPages.overview.currentReleaseLabel")}
        value={active.commit?.slice(0, 7) ?? active.id}
        mono
        detail={t.rich("appPages.overview.releaseDetail", { id: active.id, time: <RelativeTime value={active.activated_at ?? active.created_at} /> })}
      />
    );
  } else if (deploys.isPending) {
    current = <StatTile label={t("appPages.overview.currentReleaseLabel")} value={<Skeleton className="h-5 w-20" />} />;
  } else if (lastGood) {
    current = (
      <StatTile
        // In place, the checkout is not reported; what is known is the last deploy that worked.
        label={app.layout === "releases" ? t("appPages.overview.currentReleaseLabel") : t("appPages.overview.lastGoodDeployLabel")}
        value={lastGood.git_commit?.slice(0, 7) ?? `Deploy ${String(lastGood.id)}`}
        mono
        detail={
          lastGood.git_branch
            ? t.rich("appPages.overview.deployedWithBranch", { branch: lastGood.git_branch, time: <RelativeTime value={deployMoment(lastGood)} /> })
            : t.rich("appPages.overview.deployed", { time: <RelativeTime value={deployMoment(lastGood)} /> })
        }
      />
    );
  } else {
    current = <StatTile label={t("appPages.overview.lastGoodDeployLabel")} value={t("appPages.overview.noneRecorded")} detail={t("appPages.overview.noDeploySucceeded")} />;
  }

  return (
    <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
      {current}
      <StatTile
        label={t("appPages.overview.recentDeploysLabel")}
        value={
          deploys.isPending ? (
            <Skeleton className="h-5 w-28" />
          ) : items.length > 0 ? (
            <DeployDots domain={domain} deploys={items} t={t} />
          ) : (
            <span className="title text-18 text-fg">{t("appPages.overview.noneYet")}</span>
          )
        }
        detail={
          newest ? (
            t.rich("appPages.overview.lastStatusDetail", { status: deployStatus(newest.status, t.locale).label.toLowerCase(), time: <RelativeTime value={deployMoment(newest)} /> })
          ) : deploys.isPending ? undefined : (
            t("appPages.overview.deploysAppearHere")
          )
        }
      />
      <UptimeTile app={app} t={t} />
      <CertificateTile cert={findCertificate(certs.data, domain)} error={certs.error} t={t} />
    </div>
  );
}

/**
 * The newest deploy failed: said at the top, with the error's first line verbatim and the two
 * places to go next. Deliberately not live: it describes the past, and the failure was
 * announced when it happened.
 */
function LastDeployFailed({ domain, t }: { domain: string; t: T }) {
  const deploys = useQuery(deploymentsQuery({ domain, limit: DOTS }));
  const newest = deploys.data?.items[0];
  if (newest?.status !== "failed") return null;
  const line = newest.error
    ?.split("\n")
    .map((part) => part.trim())
    .find((part) => part !== "");
  return (
    <div className="flex flex-col gap-2 rounded-card border border-fail/30 bg-fail-soft/50 px-4 py-3 sm:flex-row sm:items-center sm:justify-between sm:gap-6">
      <div className="flex min-w-0 items-start gap-2.5">
        <StatusGlyph state="failed" size={14} className="mt-0.5 text-fail" />
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="text-13 font-medium text-fg">
            {t.rich("appPages.overview.lastDeployFailedAt", { time: <RelativeTime value={deployMoment(newest)} className="font-normal text-fg-muted" /> })}
          </p>
          {line !== undefined ? (
            <code translate="no" title={line} className="truncate text-12 text-fg-muted">
              {line}
            </code>
          ) : null}
        </div>
      </div>
      <div className="flex shrink-0 items-center gap-4 pl-6 sm:pl-0">
        <Link to="/apps/$domain/deployments/$id" params={{ domain, id: String(newest.id) }} className={LINK}>
          {t("appPages.overview.viewLog")}
        </Link>
        <Link to="/apps/$domain/diagnose" params={{ domain }} className={LINK}>
          {t("nav.appTabs.diagnose.label")}
        </Link>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// The sections

function kindWord(t: T, kind: string): string | null {
  if (kind === "alias") return t("appPages.overview.kindAlias");
  if (kind === "redirect") return t("appPages.overview.kindRedirect");
  return null;
}

/** The web server's own words on a site: whether it is enabled and configured for HTTPS. */
function siteStatusText(t: T, site: SiteEntry): string {
  if (site.enabled && site.has_ssl) return t("appPages.overview.siteStatus.enabledHttps", { webserver: site.webserver });
  if (site.enabled) return t("appPages.overview.siteStatus.enabledHttpOnly", { webserver: site.webserver });
  if (site.has_ssl) return t("appPages.overview.siteStatus.disabledHttps", { webserver: site.webserver });
  return t("appPages.overview.siteStatus.disabledHttpOnly", { webserver: site.webserver });
}

/** The names the app answers on, each with what the certificate does for it. */
function Domains({ app, t }: { app: App; t: T }) {
  const domain = app.domain;
  const domains = useQuery(appDomainsQuery(domain));
  const certs = useQuery(certsQuery());
  const sites = useQuery(sitesQuery());
  const lineage = certs.isError && certs.data === undefined ? null : findCertificate(certs.data, domain);
  const site = findSite(sites.data, domain);
  const entries = domains.data?.domains ?? [];

  return (
    <Section
      title={t("appPages.overview.domainsTitle")}
      actions={
        <Link to="/apps/$domain/domains" params={{ domain }} className={LINK}>
          {t("appPages.overview.manageDomains")}
        </Link>
      }
    >
      <Panel className="py-1">
        {domains.isError && domains.data === undefined ? (
          <ErrorBlock compact error={domains.error} title={t("appPages.overview.domainsLoadError")} className="my-3" />
        ) : domains.data === undefined ? (
          // One row: the app's own name, which every app has; aliases are the exception.
          <KeyValueListSkeleton rows={1} />
        ) : (
          <ul className="flex flex-col divide-y divide-border">
            {entries.map((entry) => {
              const coverage = coverageOf(entry.domain, lineage, false);
              const secure = lineage !== null && lineage !== undefined && covers(lineage, entry.domain);
              const kind = kindWord(t, entry.kind);
              return (
                <li key={entry.domain} className="flex min-h-10 flex-wrap items-center justify-between gap-x-4 gap-y-1 py-2">
                  <span className="flex min-w-0 items-center gap-2">
                    <a
                      href={`${secure ? "https" : "http"}://${entry.domain}`}
                      target="_blank"
                      rel="noreferrer"
                      translate="no"
                      className="min-w-0 truncate rounded-[4px] text-13 font-medium text-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
                    >
                      {entry.domain}
                      <span className="sr-only"> {t("appPages.sourceLink.opensInNewTab")}</span>
                    </a>
                    {kind !== null ? <Badge>{kind}</Badge> : null}
                  </span>
                  <CertificateStatus tone={coverage.tone} label={coverage.label} className="text-12" />
                </li>
              );
            })}
          </ul>
        )}
      </Panel>
      <p className="text-12 text-fg-muted">
        {site ? `${siteStatusText(t, site)} ` : site === null ? `${t("appPages.overview.siteStatus.none")} ` : ""}
        {lineage?.auto_renew ? t("appPages.overview.certificateAutoRenews") : ""}
      </p>
    </Section>
  );
}

function WebhookSection({ domain, t }: { domain: string; t: T }) {
  const deliveries = useQuery(webhookDeliveriesQuery(domain));
  const latest = deliveries.data?.items[0];
  const total = deliveries.data?.total ?? 0;
  return (
    <Section
      title={t("appPages.overview.webhookTitle")}
      description={t("appPages.overview.webhookDescription")}
      actions={
        <Link to="/apps/$domain/settings" params={{ domain }} className={LINK}>
          {t("appPages.overview.webhookSettingsLink")}
        </Link>
      }
    >
      {deliveries.isError && deliveries.data === undefined ? (
        <ErrorBlock compact error={deliveries.error} title={t("appPages.overview.webhookLoadError")} onRetry={() => void deliveries.refetch()} />
      ) : (
        <Panel className="flex min-h-12 items-center gap-3 py-3">
          <Webhook aria-hidden="true" className="size-4 shrink-0 text-fg-faint" />
          {deliveries.isPending ? (
            <Skeleton className="h-3.5 w-56" />
          ) : latest ? (
            <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1 text-13">
              <DeployStatePill status={latest.status} appearance="inline" size="sm" />
              {latest.git_commit ? (
                <span translate="no" className="mono text-12 text-fg">
                  {latest.git_commit.slice(0, 7)}
                </span>
              ) : null}
              <RelativeTime value={latest.started_at} className="text-fg-muted" />
              <span className="text-fg-faint">{t("appPages.overview.deliveriesTotal", { count: total })}</span>
            </div>
          ) : (
            <p className="text-13 text-fg-muted">{t("appPages.overview.noPushYet")}</p>
          )}
        </Panel>
      )}
    </Section>
  );
}

function Runtime({ app, t }: { app: App; t: T }) {
  const staticSite = appStatus(app.status).state === "static";
  const items: KeyValueItem[] = [
    ...(staticSite ? [] : [{ label: t("appPages.common.port"), value: app.port ?? null }]),
    { label: t("appPages.common.source"), value: sourceLink(app.source ?? null), mono: true, copy: app.source ?? false },
    { label: t("appPages.common.branch"), value: app.branch ?? null },
    ...(app.active && app.pid ? [{ label: t("appPages.common.mainPid"), value: app.pid }] : []),
    ...(staticSite ? [] : [{ label: t("appPages.common.startsAtBoot"), value: app.enabled ? t("appPages.common.yes") : t("appPages.common.no"), mono: false, copy: false as const }]),
    {
      label: t("appPages.common.layout"),
      value: app.layout === "releases" ? t("appPages.overview.layoutReleases") : t("appPages.overview.layoutInPlace"),
      mono: false,
      copy: false,
      hint: app.layout === "releases" ? t("appPages.overview.layoutReleasesHint") : t("appPages.overview.layoutInPlaceHint"),
    },
    { label: t("appPages.overview.directory"), value: app.path ?? null },
  ];
  return (
    <Section title={t("appPages.overview.runtimeTitle")}>
      <Panel className="py-1">
        {/* A fact WASM has no record of (an app deployed before it kept one), as Settings says. */}
        <KeyValueList empty={t("appPages.common.notRecorded")} items={items} />
      </Panel>
      <CommandHint command={`wasm status ${app.domain}`} label={t("appPages.fromTerminal")} />
    </Section>
  );
}

function Resources({ app, t }: { app: App; t: T }) {
  const metrics = useLatestMetrics();
  const reading = appReading(metrics, app.domain);
  const limits = appLimits(app);
  const missing = !app.active
    ? t("appPages.overview.resourceMissingNotRunning")
    : metrics === undefined
      ? t("appPages.overview.resourceMissingWaiting")
      : t("appPages.overview.resourceMissingNoReading");
  const nothing = reading.cpu === null && reading.memory === null && limits.cpu === null && limits.memory === null && limits.tasks === null;

  let body: ReactNode;
  if (nothing) {
    body = (
      <p className="text-13 text-pretty text-fg-muted">
        {app.active ? t("appPages.overview.noReadingActive") : t("appPages.overview.noReadingInactive")} {t("appPages.overview.noLimitsSet")}
      </p>
    );
  } else {
    body = (
      <>
        <ResourceMeter label={t("appPages.common.cpu")} value={reading.cpu} limit={limits.cpu} format={(value) => formatPercent(value, t.locale)} missing={missing} />
        <ResourceMeter label={t("appPages.common.memory")} value={reading.memory} limit={limits.memory} format={(value) => formatBytes(value, t.locale)} missing={missing} />
        {limits.tasks !== null ? (
          <div className="flex items-baseline justify-between gap-3 text-13">
            <span className="text-fg-muted">{t("appPages.common.tasks")}</span>
            <span className="text-fg-faint">{t("appPages.overview.limitValue", { value: formatCount(limits.tasks, t.locale) })}</span>
          </div>
        ) : null}
      </>
    );
  }

  return (
    <Section title={t("appPages.overview.resourcesTitle")} description={nothing ? undefined : t("appPages.overview.resourcesDescription")}>
      <Panel className="flex flex-col gap-4 py-4">{body}</Panel>
    </Section>
  );
}

/** A tile's lines at StatTile's own heights: the label, the reading, the line under it. */
function TileSkeleton() {
  return (
    <div className="flex min-w-0 flex-col gap-1.5 rounded-card border border-border bg-surface px-4 py-3.5 shadow-raised">
      <div className="flex h-4 items-center">
        <Skeleton className="h-3 w-20" />
      </div>
      <div className="flex h-7 items-center">
        <Skeleton className="h-5 w-28" />
      </div>
      <div className="flex h-4 items-center">
        <Skeleton className="h-3 w-24" />
      </div>
    </div>
  );
}

/** A section's heading line as it will be drawn, without being a heading itself. */
function HeadingSkeleton({ title }: { title: string }) {
  return <p className="title text-16 text-fg">{title}</p>;
}

/**
 * The loaded page's shape: the four tiles, then Domains and Runtime side by side under their
 * headings, with as many rows as they usually have. Sections that load on their own (the
 * webhook, the resources) keep their own placeholders once the page is drawn.
 */
function OverviewSkeleton({ t }: { t: T }) {
  return (
    <div aria-busy="true" className="flex flex-col gap-8">
      <span className="sr-only">{t("appPages.overview.loadingApplication")}</span>
      <div aria-hidden="true" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {[0, 1, 2, 3].map((i) => (
          <TileSkeleton key={i} />
        ))}
      </div>
      <div aria-hidden="true" className="grid gap-8 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-4">
          <HeadingSkeleton title={t("appPages.overview.domainsTitle")} />
          <div className="rounded-card border border-border bg-surface px-4 py-1 shadow-raised">
            <KeyValueListSkeleton rows={1} />
          </div>
        </div>
        <div className="flex min-w-0 flex-col gap-4">
          <HeadingSkeleton title={t("appPages.overview.runtimeTitle")} />
          <div className="rounded-card border border-border bg-surface px-4 py-1 shadow-raised">
            <KeyValueListSkeleton rows={7} hints={[5]} />
          </div>
        </div>
      </div>
    </div>
  );
}

/**
 * An app at a glance: what it runs, how its deploys went, how long it has been up, its
 * certificate; then its domains, webhook, runtime facts and resources against its limits.
 */
export function AppOverview({ domain }: { domain: string }) {
  const t = useT();
  useDocumentTitle(t("appPages.overview.documentTitle", { domain }), 1);
  const app = useQuery(appQuery(domain));

  // The layout owns the load failure and the not-found page; this shows the shape meanwhile.
  if (app.data === undefined) return app.isError ? null : <OverviewSkeleton t={t} />;

  return (
    <div className="flex flex-col gap-8">
      <div className="flex flex-col gap-3">
        <LastDeployFailed domain={domain} t={t} />
        <Tiles app={app.data} t={t} />
      </div>
      <div className="grid gap-8 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-8">
          <Domains app={app.data} t={t} />
          <WebhookSection domain={domain} t={t} />
        </div>
        <div className="flex min-w-0 flex-col gap-8">
          <Runtime app={app.data} t={t} />
          {/* A static site has no process, so nothing to measure or limit. */}
          {appStatus(app.data.status).state === "static" ? null : <Resources app={app.data} t={t} />}
        </div>
      </div>
    </div>
  );
}
