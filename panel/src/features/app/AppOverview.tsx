import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Webhook } from "lucide-react";
import type { ReactNode } from "react";

import { appQuery, releasesQuery, webhookDeliveriesQuery } from "../../api/queries/apps";
import type { App } from "../../api/queries/apps";
import { certsQuery } from "../../api/queries/certs";
import type { Cert } from "../../api/queries/certs";
import { deploymentsQuery } from "../../api/queries/deployments";
import { appDomainsQuery } from "../../api/queries/domains";
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
import { Card } from "../../components/ui/Card";
import { ICONS } from "../../components/ui/icons";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { Tooltip } from "../../components/ui/Tooltip";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatBytes, formatCount, formatDateTime, formatDuration, formatPercent, parseTimestamp } from "../../lib/format";
import type { Deployment } from "../apps/data";
import { appLimits, appReading, deployMoment, useLatestMetrics } from "../apps/data";
import { CertificateStatus } from "../domains/CertificateStatus";
import { coverageOf, covers } from "../domains/certificates";
import { CERT_WARNING_DAYS } from "../overview/attention";
import { RECENT_APP_DEPLOYS } from "./AppBanner";
import { findCertificate } from "./lookups";
import { sourceLink } from "./SourceLink";

/** How many of the app's other names the overview names before "and N more". */
const NAMES_SHOWN = 3;

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
                className={cx("flex size-7 items-center justify-center rounded-pill hover:bg-surface-hover", stateTextClass(view.state))}
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
        detail={app.enabled ? t("appPages.overview.startsAtBoot") : t("appPages.overview.doesNotStartAtBoot")}
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
  const Icon = days < 0 ? ICONS.error : days < CERT_WARNING_DAYS ? ICONS.warning : ICONS.verified;
  const tone = days < 0 ? "text-fail" : days < CERT_WARNING_DAYS ? "text-warn" : "text-ok";
  const text = days < 0 ? t("appPages.overview.certificateExpired") : t("appPages.overview.daysLeft", { count: days });
  const detail = cert.expires_on
    ? cert.auto_renew
      ? t("appPages.overview.renewsBefore", { date: cert.expires_on })
      : t("appPages.overview.validUntil", { date: cert.expires_on })
    : undefined;
  return (
    <StatTile
      label={label}
      value={
        <span className="flex items-center gap-2">
          <Icon aria-hidden="true" className={cx("size-icon-md", tone)} />
          <span className="title text-18 text-fg">{text}</span>
        </span>
      }
      detail={detail}
    />
  );
}

function Tiles({ app, t }: { app: App; t: T }) {
  const domain = app.domain;
  const deploys = useQuery(deploymentsQuery({ domain, limit: RECENT_APP_DEPLOYS }));
  const releases = useQuery({ ...releasesQuery(domain), enabled: app.layout === "releases" });
  const certs = useQuery(certsQuery());

  const items = deploys.data?.items ?? [];
  const newest = items[0];
  const lastGood = items.find((deploy) => deploy.status === "success");
  const active = releases.data?.items.find((release) => release.active);
  const liveLabel = t("appPages.overview.liveVersionLabel");

  let current: ReactNode;
  if (active) {
    current = (
      <StatTile
        label={liveLabel}
        value={active.commit?.slice(0, 7) ?? active.id}
        mono
        detail={t.rich("appPages.overview.liveSince", { time: <RelativeTime value={active.activated_at ?? active.created_at} /> })}
      />
    );
  } else if (deploys.isPending) {
    current = <StatTile label={liveLabel} value={<Skeleton className="h-5 w-20" />} />;
  } else if (lastGood) {
    current = (
      <StatTile
        label={liveLabel}
        value={lastGood.git_commit?.slice(0, 7) ?? t("appPages.overview.deployNumber", { id: String(lastGood.id) })}
        mono={lastGood.git_commit !== null && lastGood.git_commit !== undefined}
        detail={
          lastGood.git_branch
            ? t.rich("appPages.overview.deployedWithBranch", { branch: lastGood.git_branch, time: <RelativeTime value={deployMoment(lastGood)} /> })
            : t.rich("appPages.overview.deployed", { time: <RelativeTime value={deployMoment(lastGood)} /> })
        }
      />
    );
  } else {
    current = <StatTile label={liveLabel} value={t("appPages.overview.noneRecorded")} detail={t("appPages.overview.noDeploySucceeded")} />;
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
        // The glyphs' states in words too (DESIGN 6.1): how many succeeded, how the newest ended.
        detailLines={2}
        detail={
          newest ? (
            t.rich("appPages.overview.deployTally", {
              count: items.length,
              ok: formatCount(items.filter((deploy) => deployStatus(deploy.status, t.locale).state === "running").length, t.locale),
              status: deployStatus(newest.status, t.locale).label.toLowerCase(),
              time: <RelativeTime value={deployMoment(newest)} />,
            })
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

// ---------------------------------------------------------------------------------------
// The sections

/** CPU and memory now, each against the most the app may use, for an app with a process. */
function Resources({ app, t }: { app: App; t: T }) {
  const metrics = useLatestMetrics();
  const reading = appReading(metrics, app.domain);
  const limits = appLimits(app);
  const missing = !app.active
    ? t("appPages.overview.resourceMissingNotRunning")
    : metrics === undefined
      ? t("appPages.overview.resourceMissingWaiting")
      : t("appPages.overview.resourceMissingNoReading");
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
          <ResourceMeter
            label={t("appPages.common.memory")}
            value={reading.memory}
            limit={limits.memory}
            format={(value) => formatBytes(value, t.locale)}
            missing={missing}
          />
        </div>
        {limits.tasks !== null ? (
          <p className="mt-3 text-12 text-fg-muted">{t("appPages.overview.tasksLimit", { value: formatCount(limits.tasks, t.locale) })}</p>
        ) : null}
      </Card>
    </Section>
  );
}

function kindWord(t: T, kind: string): string | null {
  if (kind === "alias") return t("appPages.overview.kindAlias");
  if (kind === "redirect") return t("appPages.overview.kindRedirect");
  return null;
}

/**
 * The names the app answers on, each with whether it has HTTPS: the app's own, and the first
 * few others. The whole list, and adding to it, is the Domains tab's.
 */
function Domains({ app, t }: { app: App; t: T }) {
  const domain = app.domain;
  const domains = useQuery(appDomainsQuery(domain));
  const certs = useQuery(certsQuery());
  const lineage = certs.isError && certs.data === undefined ? null : findCertificate(certs.data, domain);
  const entries = domains.data?.domains ?? [];
  const shown = entries.slice(0, 1 + NAMES_SHOWN);
  const more = entries.length - shown.length;

  return (
    <Section
      title={t("appPages.overview.domainsTitle")}
      actions={
        <TextLink to="/apps/$domain/domains" params={{ domain }} size="ui">
          {t("appPages.overview.manageDomains")}
        </TextLink>
      }
    >
      <Card padding="none">
        {domains.isError && domains.data === undefined ? (
          <ErrorBlock compact error={domains.error} title={t("appPages.overview.domainsLoadError")} className="m-3" />
        ) : domains.data === undefined ? (
          // One row: the app's own name, which every app has; aliases are the exception.
          <div className="px-4">
            <KeyValueListSkeleton rows={1} />
          </div>
        ) : (
          <ul className="flex flex-col divide-y divide-border">
            {shown.map((entry) => {
              const coverage = coverageOf(entry.domain, lineage, false);
              const secure = lineage !== null && lineage !== undefined && covers(lineage, entry.domain);
              const kind = kindWord(t, entry.kind);
              return (
                <li key={entry.domain} className="flex min-h-10 flex-wrap items-center justify-between gap-x-4 gap-y-1 px-4 py-2">
                  <span className="flex min-w-0 items-center gap-2">
                    <a
                      href={`${secure ? "https" : "http"}://${entry.domain}`}
                      target="_blank"
                      rel="noreferrer"
                      translate="no"
                      className="min-w-0 truncate rounded-chip text-13 font-medium text-fg hover:underline hover:underline-offset-2"
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
            {more > 0 ? (
              <li className="flex min-h-10 items-center px-4 py-2 text-13 text-fg-muted">
                <TextLink to="/apps/$domain/domains" params={{ domain }} size="ui">
                  {t("appPages.overview.moreNames", { count: more })}
                </TextLink>
              </li>
            ) : null}
          </ul>
        )}
      </Card>
    </Section>
  );
}

/**
 * Whether a push deploys the app: off, on and waiting for its first push, or on with the last
 * push it received. "On" is a setting, not a state of the app: neutral, with an icon.
 */
function DeployOnPush({ app, t }: { app: App; t: T }) {
  const domain = app.domain;
  const deliveries = useQuery({ ...webhookDeliveriesQuery(domain), enabled: app.webhook_enabled });
  const latest = deliveries.data?.items[0];

  let state: string;
  let detail: ReactNode;
  if (!app.webhook_enabled) {
    state = t("appPages.overview.pushOff");
    detail = t("appPages.overview.pushOffDetail");
  } else if (deliveries.isError && deliveries.data === undefined) {
    state = t("appPages.overview.pushOn");
    detail = <ErrorBlock compact error={deliveries.error} title={t("appPages.overview.webhookLoadError")} onRetry={() => void deliveries.refetch()} />;
  } else if (deliveries.isPending) {
    state = t("appPages.overview.pushOn");
    detail = <Skeleton className="h-3.5 w-56" />;
  } else if (latest === undefined) {
    state = t("appPages.overview.pushWaiting");
    detail = app.branch
      ? t.rich("appPages.overview.pushBranch", { branch: <Mono>{app.branch}</Mono> })
      : t("appPages.overview.pushAnyBranch");
  } else {
    state = t("appPages.overview.pushOn");
    detail = (
      <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <DeployStatePill status={latest.status} appearance="inline" size="sm" />
        {latest.git_commit ? <Mono>{latest.git_commit.slice(0, 7)}</Mono> : null}
        {t.rich("appPages.overview.pushLast", { time: <RelativeTime value={latest.started_at} /> })}
      </span>
    );
  }

  return (
    <Section
      title={t("appPages.overview.pushTitle")}
      actions={
        <TextLink to="/apps/$domain/settings/deploy-on-push" params={{ domain }} size="ui">
          {app.webhook_enabled ? t("appPages.overview.pushSettings") : t("appPages.overview.pushSetUp")}
        </TextLink>
      }
    >
      <Card padding="sm">
        <div className="flex min-w-0 items-start gap-3">
          <Webhook aria-hidden="true" className="mt-0.5 size-icon-md shrink-0 text-fg-muted" />
          <div className="flex min-w-0 flex-col gap-1 text-13">
            <p className="font-medium text-fg">{state}</p>
            <div className="text-fg-muted">{detail}</div>
          </div>
        </div>
      </Card>
    </Section>
  );
}

/** The service's name as the Services page knows it: the unit without its suffix. */
function serviceName(unit: string | null | undefined): string | null {
  if (!unit) return null;
  return unit.replace(/\.service$/, "");
}

/** How the app runs, compactly: what it is built from, how its deploys work, and where it lives. */
function HowItRuns({ app, t }: { app: App; t: T }) {
  const staticSite = appStatus(app.status).state === "static";
  const service = serviceName(app.unit);
  const releases = app.layout === "releases";
  const items: KeyValueItem[] = [
    { label: t("appPages.common.source"), value: sourceLink(app.source ?? null), mono: true, copy: app.source ?? false },
    { label: t("appPages.common.branch"), value: app.branch ?? null },
    {
      label: t("appPages.overview.deploysWork"),
      value: releases ? t("appPages.overview.layoutReleases") : t("appPages.overview.layoutInPlace"),
      mono: false,
      copy: false,
      hint: releases ? t("appPages.overview.layoutReleasesHint") : t("appPages.overview.layoutInPlaceHint"),
    },
    ...(staticSite || service === null ? [] : [{ label: t("appPages.overview.service"), value: service }]),
    ...(app.active && app.pid ? [{ label: t("appPages.overview.processId"), value: app.pid }] : []),
    { label: t("appPages.overview.directory"), value: app.path ?? null },
  ];
  const noBoot = !staticSite && app.active && !app.enabled;
  return (
    <Section title={t("appPages.overview.runtimeTitle")}>
      <Card padding="none">
        <div className="px-4 py-1">
          {/* A fact Noust has no record of (an app deployed before it kept one), as Settings says. */}
          <KeyValueList empty={t("appPages.common.notRecorded")} items={items} />
        </div>
      </Card>
      {noBoot ? (
        <Notice
          tone="warning"
          title={t("appPages.overview.noBootTitle")}
          {...(service !== null
            ? {
                action: (
                  <TextLink to="/services/$name" params={{ name: service }} size="ui">
                    {t("appPages.overview.openService")}
                  </TextLink>
                ),
              }
            : {})}
        >
          {t("appPages.overview.noBootBody")}
        </Notice>
      ) : null}
    </Section>
  );
}

/**
 * The loaded page's shape: the tiles with their real labels, then the two columns under their
 * headings, with as many rows as they usually have.
 */
function OverviewSkeleton({ t }: { t: T }) {
  const value = <Skeleton className="h-5 w-24" />;
  return (
    <div aria-busy="true" className="flex flex-col gap-8">
      <span className="sr-only">{t("appPages.overview.loadingApplication")}</span>
      <div aria-hidden="true" className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        <StatTile label={t("appPages.overview.liveVersionLabel")} value={value} detail={<Skeleton className="h-3 w-28" />} />
        <StatTile label={t("appPages.overview.recentDeploysLabel")} value={value} detail={<Skeleton className="h-3 w-28" />} />
        <StatTile label={t("appPages.overview.uptimeLabel")} value={value} detail={<Skeleton className="h-3 w-20" />} />
        <StatTile label={t("appPages.overview.certificateLabel")} value={value} detail={<Skeleton className="h-3 w-28" />} />
      </div>
      <div aria-hidden="true" className="grid gap-8 lg:grid-cols-2">
        <Card padding="none">
          <div className="px-4 py-1">
            <KeyValueListSkeleton rows={2} />
          </div>
        </Card>
        <Card padding="none">
          <div className="px-4 py-1">
            <KeyValueListSkeleton rows={5} hints={[2]} />
          </div>
        </Card>
      </div>
    </div>
  );
}

/**
 * An app at a glance, summarising and linking rather than repeating the other tabs: the live
 * version, the last deploys, how long it has been up and its certificate; what it uses now
 * against its limits; then its names and whether a push deploys it, beside how it runs. What
 * is wrong with it is the banner's, above every tab.
 */
export function AppOverview({ domain }: { domain: string }) {
  const t = useT();
  useDocumentTitle(t("appPages.overview.documentTitle", { domain }), 1);
  const app = useQuery(appQuery(domain));

  // The layout owns the load failure and the not-found page; this shows the shape meanwhile.
  if (app.data === undefined) return app.isError ? null : <OverviewSkeleton t={t} />;
  const staticSite = appStatus(app.data.status).state === "static";

  return (
    <div className="flex flex-col gap-8">
      <Tiles app={app.data} t={t} />
      {/* A static site has no process, so nothing to measure or limit. */}
      {staticSite ? null : <Resources app={app.data} t={t} />}
      <div className="grid gap-8 lg:grid-cols-2">
        <div className="flex min-w-0 flex-col gap-8">
          <Domains app={app.data} t={t} />
          <DeployOnPush app={app.data} t={t} />
        </div>
        <HowItRuns app={app.data} t={t} />
      </div>
      <CommandHint command={`noust status ${domain}`} label={t("appPages.fromTerminal")} />
    </div>
  );
}
