/**
 * The web application firewall (ModSecurity with the OWASP Core Rule Set) as the machine's: whether the server can run
 * it, and, for each site, the level it was given and what the rules did today. The firewall is turned on, off or to
 * "only write down" in the site's own protection settings (it is the site's policy, saved with the rest of it);
 * this page is where it is looked at as a whole, which the machine's pieces of protection had no page for.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useMemo } from "react";

import { appsQuery } from "../../../api/queries/apps";
import { appPolicyQuery, appWafEventsQuery, appWafQuery } from "../../../api/queries/network";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { FirstUse } from "../../../components/ui/FirstUse";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { StatusPill } from "../../../components/ui/StatusPill";
import { textLinkClassName } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { parseTimestamp } from "../../../lib/format";
import { ServerErrorBlock } from "../errors";
import { MissingSoftware } from "../software/MissingSoftware";

export type WafLevel = "off" | "detect" | "block";

/** The level a site's policy gives the firewall: anything it does not name is off. */
export function wafLevelOf(waf: string | null | undefined): WafLevel {
  return waf === "detect" || waf === "block" ? waf : "off";
}

/** How many of the requests the firewall judged are ones it refused, from the start of today. */
export function blocksToday(events: readonly { blocked: boolean; time: string }[], now: Date = new Date()): number {
  const start = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
  return events.filter((event) => event.blocked && (parseTimestamp(event.time)?.getTime() ?? 0) >= start).length;
}

function levelWords(t: T, level: WafLevel): string {
  return level === "block" ? t("server.waf.levelBlock") : level === "detect" ? t("server.waf.levelDetect") : t("server.waf.levelOff");
}

/** A site's level, from its own policy; nothing is said while it is read, and not that it is off when it could not be. */
function LevelCell({ domain }: { domain: string }) {
  const t = useT();
  const policy = useQuery(appPolicyQuery(domain));
  if (policy.data === undefined) return <EmptyCell {...(policy.isError ? { reason: t("server.waf.notRead") } : {})} />;
  const level = wafLevelOf(policy.data.policy.waf);
  return <span className="text-13 text-fg">{levelWords(t, level)}</span>;
}

/** What the rules refused today for a site: only a site that has the firewall on has anything to count. */
function BlocksCell({ domain }: { domain: string }) {
  const t = useT();
  const policy = useQuery(appPolicyQuery(domain));
  const on = policy.data !== undefined && wafLevelOf(policy.data.policy.waf) !== "off";
  const events = useQuery({ ...appWafEventsQuery(domain), enabled: on });
  if (!on || events.data === undefined) return <EmptyCell {...(on && events.isError ? { reason: t("server.waf.notRead") } : {})} />;
  return <Mono className="text-13">{blocksToday(events.data.events)}</Mono>;
}

/** The way to the site's protection settings, worded by whether the firewall is on there. */
function ChangeLink({ domain }: { domain: string }) {
  const t = useT();
  const policy = useQuery(appPolicyQuery(domain));
  const off = policy.data !== undefined && wafLevelOf(policy.data.policy.waf) === "off";
  return (
    <Link to="/apps/$domain/access" params={{ domain }} hash="protection" className={textLinkClassName("inline", "text-13 whitespace-nowrap")} aria-label={t(off ? "server.waf.turnOnNamed" : "server.waf.changeNamed", { domain })}>
      {off ? t("server.waf.turnOn") : t("server.waf.change")}
    </Link>
  );
}

interface SiteRow {
  domain: string;
}

function Sites() {
  const t = useT();
  const apps = useQuery(appsQuery());
  const rows = useMemo<SiteRow[]>(() => (apps.data?.apps ?? []).map((app) => ({ domain: app.domain })), [apps.data]);
  const columns: Column<SiteRow>[] = [
    { id: "site", header: t("server.waf.site"), card: "title", sortValue: (row) => row.domain, cell: (row) => <Mono className="font-medium">{row.domain}</Mono> },
    { id: "level", header: t("server.waf.level"), card: "status", cell: (row) => <LevelCell domain={row.domain} /> },
    { id: "blocks", header: t("server.waf.blocksToday"), align: "end", hideBelow: "sm", card: "meta", cell: (row) => <BlocksCell domain={row.domain} />, mono: true },
  ];
  return (
    <SurfaceSection title={t("server.waf.perSite")} icon={OBJECT_ICONS.protection} description={t("server.waf.perSiteDescription")} flush={rows.length > 0}>
      {apps.isError ? (
        <ServerErrorBlock compact error={apps.error} title={t("server.waf.sitesFailed")} onRetry={() => void apps.refetch()} />
      ) : rows.length === 0 && !apps.isPending ? (
        <FirstUse>{t("server.waf.noSites")}</FirstUse>
      ) : (
        <DataTable
          embedded
          columns={columns}
          rows={apps.isPending ? undefined : rows}
          getRowId={(row) => row.domain}
          caption={t("server.waf.caption")}
          loading={apps.isPending}
          skeletonRows={3}
          density="compact"
          pagination="waf"
          rowActions={(row) => <ChangeLink domain={row.domain} />}
        />
      )}
    </SurfaceSection>
  );
}

/** Whether the server can run the firewall, from the first site's own answer (it is the server's, whichever site asks). */
export function useWafSupport() {
  const apps = useQuery(appsQuery());
  const first = apps.data?.apps[0]?.domain;
  return useQuery({ ...appWafQuery(first ?? ""), enabled: first !== undefined });
}

/** The state the title carries: working, or what stands in the way. */
export function WafState() {
  const t = useT();
  const support = useWafSupport();
  if (support.data === undefined) return null;
  return support.data.available ? <StatusPill state="running" label={t("server.waf.active")} size="sm" /> : <StatusPill state="stopped" label={t("server.waf.notAvailable")} size="sm" />;
}

/** The WAF section. */
export function WafView() {
  const t = useT();
  const support = useWafSupport();
  const unavailable = support.data !== undefined && !support.data.available;
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {unavailable ? (
        <MissingSoftware
          component="waf"
          fallback={
            <div className="flex flex-col gap-1 text-13 text-fg-muted">
              <p className="font-medium text-fg">{support.data.problem}</p>
              <p>{support.data.hint}</p>
            </div>
          }
        />
      ) : null}
      {support.isError ? <ServerErrorBlock compact error={support.error} title={t("server.waf.readFailed")} /> : null}
      <Sites />
    </div>
  );
}
