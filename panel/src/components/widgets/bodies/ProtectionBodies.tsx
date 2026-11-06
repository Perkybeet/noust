/**
 * The widgets about what keeps the server safe: the certificates that are about to expire, the backups and
 * the days they were taken, and the probes that watch from outside. Only what is not well is coloured; a
 * server whose certificates all renew and whose backups ran is a quiet slab.
 */
import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { backupsQuery } from "../../../api/queries/backups";
import { certsQuery } from "../../../api/queries/certs";
import { overviewQuery } from "../../../api/queries/overview";
import { probesQuery } from "../../../api/queries/probes";
import { fleetViewQuery, numberOf, objectOf, textOf } from "../../../features/fleet/data";
import { backupDays, certificatesOf, latestBackup } from "../../../features/overview/widgetData";
import { useT } from "../../../i18n";
import { cx } from "../../../lib/cx";
import { formatCount } from "../../../lib/format";
import { RelativeTime } from "../../page/RelativeTime";
import { StatusGlyph } from "../../ui/StatusPill";
import { Mono } from "../../ui/Mono";
import { Big, Glyph, StateLine, WidgetPending } from "../parts";
import type { WidgetBodyProps } from "../types";
import { useCan } from "../../page/permissions";

// ---------------------------------------------------------------------------------------
// Certificates

/** Days of a certificate's life drawn as the whole of its bar: the longest a certificate is issued for. */
const BAR_DAYS = 90;

/** What each server of the fleet says of its own certificates, from its own Overview inside the summary. */
function useFleetFigures(key: "certificates" | "backups", enabled: boolean) {
  const summary = useQuery({ ...fleetViewQuery("summary"), enabled });
  const rows = (summary.data?.items ?? []).map((row) => ({ server: textOf(row["name"]) ?? textOf(row["node"]) ?? "", figure: objectOf(objectOf(row["overview"])?.[key]) }));
  return { summary, rows };
}

/** The certificates that expire soon, soonest first, and how many are well; for the fleet, how many and on which server. */
export function CertificatesBody({ scope }: WidgetBodyProps) {
  const t = useT();
  const certs = useQuery({ ...certsQuery(), enabled: scope === "server" });
  const fleet = useFleetFigures("certificates", scope === "fleet");
  const title = t("widgets.certificates.title");
  if (scope === "fleet") {
    if (fleet.summary.data === undefined) return <WidgetPending title={title} failed={fleet.summary.isError} onRetry={() => void fleet.summary.refetch()} />;
    const needing = fleet.rows.flatMap((row) => {
      const count = (numberOf(row.figure?.["expiring"]) ?? 0) + (numberOf(row.figure?.["expired"]) ?? 0);
      return count > 0 ? [{ server: row.server, count, days: numberOf(row.figure?.["next_days"]) }] : [];
    });
    const total = fleet.rows.reduce((sum, row) => sum + (numberOf(row.figure?.["total"]) ?? 0), 0);
    const count = needing.reduce((sum, row) => sum + row.count, 0);
    return (
      <div className="flex h-full flex-col gap-2">
        <div className="flex items-baseline gap-2">
          <Big className="flex items-center gap-1.5">
            {count > 0 ? <Glyph state="warning" size={14} /> : null}
            {formatCount(count, t.locale)}
          </Big>
          <span className="text-13 text-fg-muted">{t("widgets.certificates.expiring")}</span>
        </div>
        <ul className="flex flex-col gap-1">
          {needing.slice(0, 4).map((row) => (
            <li key={row.server} className="flex items-baseline gap-2 text-12 text-fg">
              <span translate="no" className="font-medium">
                {row.server}
              </span>
              {row.days !== null ? <span className="text-fg-muted">{t("widgets.certificates.inDays", { count: row.days })}</span> : null}
            </li>
          ))}
        </ul>
        <p className="mt-auto text-12 text-fg-faint">{t("widgets.certificates.total", { count: total })}</p>
      </div>
    );
  }
  if (certs.data === undefined) return <WidgetPending title={title} failed={certs.isError} onRetry={() => void certs.refetch()} />;
  const view = certificatesOf(certs.data.certificates);
  const next = view.needing[0];
  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <Big className="flex items-center gap-1.5">
          {view.needing.length > 0 ? <Glyph state="warning" size={14} /> : null}
          {formatCount(view.needing.length, t.locale)}
        </Big>
        <span className="text-13 text-fg-muted">{t("widgets.certificates.expiring")}</span>
      </div>
      {next !== undefined ? (
        <p className="truncate text-12 text-fg-muted w-wide:hidden">
          <span translate="no" className="font-medium text-fg">
            {next.domain}
          </span>{" "}
          {t("widgets.certificates.inDays", { count: next.days })}
        </p>
      ) : null}
      <p className="text-12 text-fg-faint w-wide:hidden">{view.needing.length > 0 ? t("widgets.certificates.others", { count: view.well }) : t("widgets.certificates.renewing", { total: view.total, renewing: view.renewing })}</p>
      <ul className="hidden flex-col gap-2 overflow-hidden w-wide:flex">
        {view.needing.slice(0, 6).map((row) => (
          <li key={row.domain} className="flex items-center gap-3 text-13">
            <span translate="no" className="w-40 shrink-0 truncate font-medium text-fg">
              {row.domain}
            </span>
            <span className="elev-carved-fine relative h-1.5 min-w-0 flex-1 overflow-hidden rounded-pill bg-bg-sunken">
              <span className="absolute inset-y-0 left-0 rounded-pill bg-warn" style={{ width: `${String(Math.max(2, Math.min(100, (row.days / BAR_DAYS) * 100)))}%` }} />
            </span>
            <span className="w-16 shrink-0 text-right text-12 font-medium text-fg tabular-nums">{t("widgets.certificates.days", { count: row.days })}</span>
          </li>
        ))}
      </ul>
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Backups

/** The last backup, the ones that failed, and the days of the last two weeks that have a copy. */
export function BackupsBody({ scope }: WidgetBodyProps) {
  const t = useT();
  const can = useCan();
  const backups = useQuery({ ...backupsQuery(), enabled: scope === "server" });
  // What failed is the Overview's to say, and only a role that may read the server can ask it.
  const overview = useQuery({ ...overviewQuery(), enabled: scope === "server" && can("server.read") });
  const fleet = useFleetFigures("backups", scope === "fleet");
  const title = t("widgets.backups.title");
  if (scope === "fleet") {
    if (fleet.summary.data === undefined) return <WidgetPending title={title} failed={fleet.summary.isError} onRetry={() => void fleet.summary.refetch()} />;
    const failed = fleet.rows.flatMap((row) => ((numberOf(row.figure?.["failed_24h"]) ?? 0) > 0 ? [row.server] : []));
    const apps = fleet.rows.reduce((sum, row) => sum + (numberOf(row.figure?.["apps"]) ?? 0), 0);
    const done = fleet.rows.reduce((sum, row) => sum + (numberOf(row.figure?.["with_backup_24h"]) ?? 0), 0);
    const last = fleet.rows.map((row) => textOf(row.figure?.["last_at"])).filter((value): value is string => value !== null).sort().at(-1) ?? null;
    return (
      <div className="flex h-full flex-col gap-2">
        {last !== null ? (
          <p className="flex items-center gap-1.5 text-13 font-medium text-fg">
            <Glyph state="running" />
            {t("widgets.backups.last")} <RelativeTime value={last} />
          </p>
        ) : (
          <StateLine state="stopped">{t("widgets.backups.none")}</StateLine>
        )}
        <p className="text-12 text-fg-muted">{t("widgets.backups.covered", { done, total: apps })}</p>
        {failed.length > 0 ? <StateLine state="warning">{t("widgets.backups.failedOn", { servers: failed.join(", ") })}</StateLine> : null}
      </div>
    );
  }
  if (backups.data === undefined) return <WidgetPending title={title} failed={backups.isError} onRetry={() => void backups.refetch()} />;
  const last = latestBackup(backups.data.backups);
  const days = backupDays(backups.data.backups, new Date());
  const failed = overview.data?.backups.failed_24h ?? 0;
  return (
    <div className="flex h-full flex-col gap-2">
      {last !== null ? (
        <p className="flex items-center gap-1.5 text-13 font-medium text-fg">
          <Glyph state="running" />
          {t("widgets.backups.last")} <RelativeTime value={last.at} />
        </p>
      ) : (
        <StateLine state="stopped">{t("widgets.backups.none")}</StateLine>
      )}
      {last?.verified === true ? <p className="text-12 text-fg-muted">{t("widgets.backups.verified")}</p> : null}
      {failed > 0 ? <StateLine state="warning">{t("widgets.backups.failedToday", { count: failed })}</StateLine> : null}
      <div className="mt-auto hidden w-mid:block">
        <p className="mb-1 text-12 text-fg-faint">{t("widgets.backups.lastDays", { count: days.length })}</p>
        <div role="img" aria-label={t("widgets.backups.daysLabel", { taken: days.filter((day) => day.count > 0).length, count: days.length })} className="flex gap-0.5">
          {days.map((day) => (
            <span key={day.date} className={cx("flex h-5 min-w-0 flex-1 rounded-chip", day.count > 0 ? "bg-il-stroke-2" : "elev-carved-fine bg-bg-sunken")} />
          ))}
        </div>
      </div>
    </div>
  );
}

// ---------------------------------------------------------------------------------------
// Probes

/** How the probes that watch from outside stand: how many answer, and the ones that do not. */
export function ProbesBody() {
  const t = useT();
  const probes = useQuery(probesQuery());
  if (probes.data === undefined) return <WidgetPending title={t("widgets.probes.title")} failed={probes.isError} onRetry={() => void probes.refetch()} />;
  const enabled = probes.data.items.filter((probe) => probe.enabled);
  if (enabled.length === 0) return <p className="text-13 text-fg-muted">{t("widgets.probes.none")}</p>;
  const down = enabled.filter((probe) => probe.status.state === "down");
  return (
    <div className="flex h-full flex-col gap-2">
      <div className="flex items-baseline gap-2">
        <Big className="flex items-center gap-1.5">
          {down.length > 0 ? <StatusGlyph state="failed" size={14} className="text-fail" /> : null}
          {formatCount(enabled.length - down.length, t.locale)}
        </Big>
        <span className="text-13 text-fg-muted">{t("widgets.probes.answering", { total: enabled.length })}</span>
      </div>
      <ul className="flex min-h-0 flex-col gap-1 overflow-hidden">
        {down.slice(0, 3).map((probe) => (
          <li key={probe.name} className="flex items-center gap-2 text-12 text-fg">
            <Glyph state="failed" size={10} />
            <Link to="/alerts/probes" className="min-w-0 truncate hover:underline">
              <Mono>{probe.name}</Mono>
            </Link>
          </li>
        ))}
      </ul>
    </div>
  );
}
