import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";

import type { Overview } from "../../api/queries/overview";
import { RelativeTime } from "../../components/page/RelativeTime";
import { StatTile } from "../../components/page/StatTile";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes, formatCount, formatPercent } from "../../lib/format";
import { appsTone, backupsTone, certificatesTone, deploysTone, diskTone, updatesTone } from "./overviewData";
import type { FigureTone } from "./overviewData";

const TILE = "block min-w-0 rounded-card";
const TILE_SURFACE = "h-full transition-colors duration-(--duration-fast) ease-out hover:border-border-strong";

/** A count with its state's glyph and colour: only a problem is coloured, and never by colour alone. */
function StateCount({ state, children }: { state: Status; children: ReactNode }) {
  return (
    // Inline, not a flex box: a long count wraps with the sentence around it, the glyph kept
    // before its first word.
    <span className={stateTextClass(state)}>
      {typeof children === "string" ? (
        // The glyph never ends a line apart from the word it marks.
        <>
          <span className="whitespace-nowrap">
            <StatusGlyph state={state} size={10} className="mr-1 inline-block" />
            {children.split(" ")[0]}
          </span>
          {children.includes(" ") ? children.slice(children.indexOf(" ")) : null}
        </>
      ) : (
        <>
          <StatusGlyph state={state} size={10} className="mr-1 inline-block" />
          {children}
        </>
      )}
    </span>
  );
}

/**
 * Parts of a detail line, a middle dot apart, flowing as one sentence: in a narrow tile they
 * wrap onto the second line the tile keeps for them, instead of each part wrapping on its own.
 */
function Parts({ parts }: { parts: readonly ReactNode[] }) {
  const shown = parts.filter((part) => part !== null && part !== false);
  return (
    <span>
      {shown.map((part, index) => (
        // A fixed, short list rebuilt on every render: its position is its identity.
        <span key={index}>
          {index > 0 ? <span aria-hidden="true">{" · "}</span> : null}
          {part}
        </span>
      ))}
    </span>
  );
}

/** The value of a figure, with the glyph of its state when it is a warning or a failure. */
function Value({ tone, children, word }: { tone: FigureTone; children: ReactNode; word?: string | undefined }) {
  const state: Status | null = tone === "fail" ? "failed" : tone === "warn" ? "warning" : null;
  return (
    <span className="flex min-w-0 items-center gap-2">
      {state !== null ? <StatusGlyph state={state} size={14} className={stateTextClass(state)} /> : null}
      <span className="title truncate text-18 text-fg">{children}</span>
      {state !== null && word !== undefined ? <span className={`shrink-0 text-12 ${stateTextClass(state)}`}>{word}</span> : null}
    </span>
  );
}

/** Why a figure could not be read, in the tool's own words. */
function Unreadable({ error }: { error: string }) {
  return (
    <Mono truncate title={error}>
      {error}
    </Mono>
  );
}

interface Figure {
  key: string;
  to: "/apps" | "/activity" | "/domains" | "/backups" | "/server";
  label: string;
  aria: string;
  value: ReactNode;
  detail: ReactNode;
}

function appsFigure(t: T, apps: Overview["apps"]): Figure {
  const label = t("overview.figures.apps.label");
  if (apps.error) return unreadable(t, "apps", "/apps", label, apps.error);
  const total = apps.running + apps.failed + apps.stopped + apps.static;
  const tone = appsTone(apps);
  return {
    key: "apps",
    to: "/apps",
    label,
    aria: t("overview.figures.apps.aria", { running: apps.running, failed: apps.failed, stopped: apps.stopped, static: apps.static }),
    value: <Value tone="neutral">{total === 0 ? t("overview.figures.apps.noneYet") : t("overview.figures.apps.running", { count: formatCount(apps.running, t.locale) })}</Value>,
    detail:
      apps.failed + apps.stopped === 0 ? (
        total === 0 ? null : apps.static > 0 ? t("overview.figures.apps.static", { count: apps.static }) : t("overview.figures.apps.allRunning")
      ) : (
        <Parts
          parts={[
            apps.failed > 0 ? <StateCount state={tone === "fail" ? "failed" : "unknown"}>{t("overview.figures.apps.failed", { count: apps.failed })}</StateCount> : null,
            apps.stopped > 0 ? <StateCount state="stopped">{t("overview.figures.apps.stopped", { count: apps.stopped })}</StateCount> : null,
          ]}
        />
      ),
  };
}

function deploysFigure(t: T, deploys: Overview["deploys"]): Figure {
  const label = t("overview.figures.deploys.label");
  if (deploys.error) return unreadable(t, "deploys", "/activity", label, deploys.error);
  const tone = deploysTone(deploys);
  return {
    key: "deploys",
    to: "/activity",
    label,
    aria: t("overview.figures.deploys.aria", { total: deploys.total, succeeded: deploys.succeeded, failed: deploys.failed }),
    value: <Value tone="neutral">{deploys.total === 0 ? t("overview.figures.deploys.none") : formatCount(deploys.total, t.locale)}</Value>,
    detail:
      deploys.total === 0 ? (
        deploys.last_at ? (
          <LastTime t={t} value={deploys.last_at} kind="deploys" />
        ) : null
      ) : (
        <Parts
          parts={[
            deploys.succeeded > 0 ? t("overview.figures.deploys.succeeded", { count: deploys.succeeded }) : null,
            deploys.failed > 0 ? (
              <StateCount state={tone === "fail" ? "failed" : "warning"}>{t("overview.figures.deploys.failed", { count: deploys.failed })}</StateCount>
            ) : null,
            deploys.running > 0 ? <StateCount state="deploying">{t("overview.figures.deploys.running", { count: deploys.running })}</StateCount> : null,
          ]}
        />
      ),
  };
}

function LastTime({ t, value, kind }: { t: T; value: string; kind: "deploys" | "backups" }) {
  const time = <RelativeTime value={value} />;
  return <span>{t.rich(kind === "deploys" ? "overview.figures.deploys.last" : "overview.figures.backups.last", { time })}</span>;
}

function certificatesFigure(t: T, certs: Overview["certificates"]): Figure {
  const label = t("overview.figures.certificates.label");
  if (certs.error) return unreadable(t, "certificates", "/domains", label, certs.error);
  const tone = certificatesTone(certs);
  const value =
    certs.expired > 0
      ? t("overview.figures.certificates.expired", { count: certs.expired })
      : certs.expiring > 0
        ? t("overview.figures.certificates.expiring", { count: certs.expiring })
        : certs.total === 0
          ? t("overview.figures.certificates.none")
          : t("overview.figures.certificates.allValid");
  const next = certs.next_days ?? null;
  return {
    key: "certificates",
    to: "/domains",
    label,
    aria: t("overview.figures.certificates.aria", { total: certs.total, expiring: certs.expiring, expired: certs.expired }),
    value: <Value tone={tone}>{value}</Value>,
    detail:
      certs.total === 0
        ? t("overview.figures.certificates.noneDetail")
        : next !== null && next >= 0 && tone !== "neutral"
          ? t("overview.figures.certificates.nextIn", { count: next })
          : t("overview.figures.certificates.total", { count: certs.total }),
  };
}

function backupsFigure(t: T, backups: Overview["backups"]): Figure {
  const label = t("overview.figures.backups.label");
  if (backups.error) return unreadable(t, "backups", "/backups", label, backups.error);
  const tone = backupsTone(backups);
  const aria = t("overview.figures.backups.aria", { done: backups.with_backup_24h, total: backups.apps, failed: backups.failed_24h });
  if (backups.scheduled === 0 && backups.with_backup_24h === 0) {
    return {
      key: "backups",
      to: "/backups",
      label,
      aria,
      value: <Value tone="neutral">{t("overview.figures.backups.notScheduled")}</Value>,
      detail: t("overview.figures.backups.notScheduledDetail"),
    };
  }
  return {
    key: "backups",
    to: "/backups",
    label,
    aria,
    value: <Value tone={tone}>{t("overview.figures.backups.ofApps", { done: backups.with_backup_24h, total: backups.apps })}</Value>,
    detail:
      backups.failed_24h > 0 ? (
        <StateCount state="failed">{t("overview.figures.backups.failed", { count: backups.failed_24h })}</StateCount>
      ) : backups.unprotected_scheduled > 0 ? (
        <StateCount state="warning">{t("overview.figures.backups.unprotected", { count: backups.unprotected_scheduled })}</StateCount>
      ) : backups.last_at ? (
        <LastTime t={t} value={backups.last_at} kind="backups" />
      ) : (
        t("overview.figures.backups.never")
      ),
  };
}

function diskFigure(t: T, disk: Overview["disk"]): Figure {
  const label = t("overview.figures.disk.label");
  if (disk.error) return unreadable(t, "disk", "/server", label, disk.error);
  const tone = diskTone(disk);
  const percent = formatPercent(disk.free_percent, t.locale);
  const used = formatBytes(disk.used, t.locale);
  const total = formatBytes(disk.total, t.locale);
  const days = disk.forecast_full_days ?? null;
  const reason = disk.forecast_reason?.code ?? null;
  return {
    key: "disk",
    to: "/server",
    label,
    aria: t("overview.figures.disk.aria", { percent, used, total }),
    value: (
      <Value tone={tone} word={tone === "fail" ? t("overview.figures.disk.critical") : tone === "warn" ? t("overview.figures.disk.high") : undefined}>
        {t("overview.figures.disk.free", { percent })}
      </Value>
    ),
    detail:
      days !== null
        ? t("overview.figures.disk.fullIn", { count: days })
        : reason === "not_growing"
          ? t("overview.figures.disk.notGrowing")
          : t("overview.figures.disk.used", { used, total }),
  };
}

function updatesFigure(t: T, updates: Overview["updates"]): Figure {
  const label = t("overview.figures.updates.label");
  if (updates.error) return unreadable(t, "updates", "/server", label, updates.error);
  const tone = updatesTone(updates);
  let value: string;
  let detail: ReactNode = null;
  if (updates.supported === false) {
    value = t("overview.figures.updates.unsupported");
    detail = <span title={updates.reason?.message ?? undefined}>{t("overview.figures.updates.unsupportedDetail")}</span>;
  } else if (updates.available === null || updates.available === undefined) {
    value = t("overview.figures.updates.notChecked");
  } else if (updates.available === 0) {
    value = t("overview.figures.updates.upToDate");
  } else {
    value = t("overview.figures.updates.pending", { count: updates.available });
  }
  if (updates.supported !== false) {
    detail = (
      <Parts
        parts={[
          (updates.security ?? 0) > 0 ? <StateCount state="warning">{t("overview.figures.updates.security", { count: updates.security ?? 0 })}</StateCount> : null,
          updates.reboot_required === true ? <StateCount state="warning">{t("overview.figures.updates.reboot")}</StateCount> : null,
          (updates.security ?? 0) === 0 && updates.reboot_required !== true && updates.checked_at ? (
            <span>{t.rich("overview.figures.updates.checked", { time: <RelativeTime value={updates.checked_at} /> })}</span>
          ) : null,
        ]}
      />
    );
  }
  return {
    key: "updates",
    to: "/server",
    label,
    aria: t("overview.figures.updates.aria", { state: value }),
    value: <Value tone={tone}>{value}</Value>,
    detail,
  };
}

function unreadable(t: T, key: string, to: Figure["to"], label: string, error: string): Figure {
  return {
    key,
    to,
    label,
    aria: `${label}: ${t("overview.figures.couldNotRead")}`,
    value: <Value tone="neutral">{t("overview.figures.couldNotRead")}</Value>,
    detail: <Unreadable error={error} />,
  };
}

/** The six figures of a server, as tiles that each open the page they come from. */
export function KeyFigures({ overview }: { overview: Overview | undefined }) {
  const t = useT();
  if (overview === undefined) {
    const labels = [
      t("overview.figures.apps.label"),
      t("overview.figures.deploys.label"),
      t("overview.figures.certificates.label"),
      t("overview.figures.backups.label"),
      t("overview.figures.disk.label"),
      t("overview.figures.updates.label"),
    ];
    return (
      <>
        {labels.map((label) => (
          <StatTile
            key={label}
            label={label}
            value={<Skeleton className="h-4.5 w-24" />}
            detailLines={2}
            // As tall as the line of text it stands for.
            detail={
              <span className="flex h-4 items-center">
                <Skeleton className="h-3 w-32 max-w-full" />
              </span>
            }
          />
        ))}
      </>
    );
  }
  const figures = [
    appsFigure(t, overview.apps),
    deploysFigure(t, overview.deploys),
    certificatesFigure(t, overview.certificates),
    backupsFigure(t, overview.backups),
    diskFigure(t, overview.disk),
    updatesFigure(t, overview.updates),
  ];
  return (
    <>
      {figures.map((figure) => (
        <Link key={figure.key} to={figure.to} aria-label={figure.aria} className={TILE} data-figure={figure.key}>
          <StatTile label={figure.label} value={figure.value} detail={figure.detail ?? undefined} detailLines={2} className={TILE_SURFACE} />
        </Link>
      ))}
    </>
  );
}
