/**
 * The server in one screen: what needs attention first, worst first, each with the action
 * that deals with it; then the machine's facts and five readings. It summarises and links:
 * every list it draws from is a tab of its own.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { useState } from "react";

import { machineQuery } from "../../../api/queries/system";
import { CommandHint } from "../../../components/page/CommandHint";
import { KeyValueList, KeyValueListSkeleton } from "../../../components/page/KeyValueList";
import type { KeyValueItem } from "../../../components/page/KeyValueList";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { StatTile } from "../../../components/page/StatTile";
import { Button, buttonClassName } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes, formatBytesPair, formatDate, formatDuration, formatPercent, parseTimestamp } from "../../../lib/format";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { attentionItems } from "../attention";
import type { AttentionItem } from "../attention";
import { ServerErrorBlock } from "../errors";
import { usePowerDialog } from "../PowerDialog";
import { SERVER_CAPABILITY, dayOf, securityOverviewQuery, summaryQuery } from "../queries";
import type { SecurityOverview, ServerSummary } from "../queries";
import { checkTitle } from "../security/data";

/** Rows shown before "Show all": the three most serious and two more fit the card. */
const SHOWN = 5;

function day(value: string | null | undefined, t: T): string | null {
  const date = dayOf(value) ?? parseTimestamp(value ?? null);
  return date ? formatDate(date, {}, t.locale) : (value ?? null);
}

/** What an item says, in the active language; a check's reason stays in Noust's words, under it. */
export function attentionText(t: T, item: AttentionItem): string {
  switch (item.kind) {
    case "securityUpdates":
      return t("server.attention.securityUpdates", { count: item.count });
    case "packagesBroken":
      return t("server.attention.packagesBroken");
    case "rebootRequired":
      return item.packages.length > 0
        ? t("server.attention.rebootRequiredBecause", { packages: item.packages.slice(0, 3).join(", ") })
        : t("server.attention.rebootRequired");
    case "osUnsupported":
      return item.date !== null ? t("server.attention.osUnsupportedSince", { name: item.name, date: day(item.date, t) ?? item.date }) : t("server.attention.osUnsupported", { name: item.name });
    case "osEnding":
      return t("server.attention.osEnding", { name: item.name, count: item.days });
    case "diskFull":
      return item.free !== null
        ? t("server.attention.diskFullFree", { mount: item.mount, percent: formatPercent(item.percent, t.locale), free: formatBytes(item.free, t.locale) })
        : t("server.attention.diskFull", { mount: item.mount, percent: formatPercent(item.percent, t.locale) });
    case "clockUnsynced":
      return t("server.attention.clockUnsynced");
    case "noSwap":
      return t("server.attention.noSwap");
    case "failedUnits":
      return t("server.attention.failedUnits", { count: item.units.length, units: item.units.slice(0, 3).join(", ") });
    case "check":
      return checkTitle(t, item.check);
  }
}

function actionLabel(t: T, item: AttentionItem): string {
  switch (item.kind) {
    case "securityUpdates":
      return t("server.attention.actions.install");
    case "packagesBroken":
      return t("server.attention.actions.repair");
    case "rebootRequired":
      return t("server.attention.actions.scheduleReboot");
    case "osUnsupported":
    case "osEnding":
      return t("server.attention.actions.view");
    case "diskFull":
      return t("server.attention.actions.freeSpace");
    case "clockUnsynced":
      return t("server.attention.actions.fixClock");
    case "noSwap":
      return t("server.attention.actions.addSwap");
    case "failedUnits":
      return t("server.attention.actions.viewServices");
    case "check":
      return t("server.attention.actions.review");
  }
}

function AttentionAction({ item }: { item: AttentionItem }) {
  const t = useT();
  const power = usePowerDialog();
  const label = actionLabel(t, item);
  const className = buttonClassName("secondary", "sm", "shrink-0");
  switch (item.action.kind) {
    case "reboot":
      return (
        <Button size="sm" className="shrink-0" onClick={() => power.open("reboot")}>
          {label}
        </Button>
      );
    case "services":
      return (
        <Link to="/server/services" search={{ state: item.action.state, all: true }} className={className}>
          {label}
        </Link>
      );
    case "tab":
      return item.action.to === "/server/security" ? (
        <Link to="/server/security" search={item.action.view !== undefined && item.action.view !== "checks" ? { view: item.action.view as "ssh" } : {}} className={className}>
          {label}
        </Link>
      ) : (
        <Link to={item.action.to} className={className}>
          {label}
        </Link>
      );
  }
}

function AttentionRow({ item }: { item: AttentionItem }) {
  const t = useT();
  const state = item.severity === "critical" ? "failed" : "warning";
  return (
    <li className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1.5 px-5 py-2.5">
      <StatusGlyph state={state} size={12} className={`shrink-0 ${stateTextClass(state)}`} />
      <span className="sr-only">{item.severity === "critical" ? t("server.attention.critical") : t("server.attention.warning")}</span>
      <div className="flex min-w-0 flex-1 basis-60 flex-col">
        <span className="text-13 text-fg">{attentionText(t, item)}</span>
        {/* A check's reason is the server's English prose: the Security tab shows it in full. */}
      </div>
      <AttentionAction item={item} />
    </li>
  );
}

function NeedsAttention({ items, loading }: { items: readonly AttentionItem[]; loading: boolean }) {
  const t = useT();
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, SHOWN);
  return (
    <Card level={2}
      title={t("server.attention.title")}
      padding="none"
      className="lg:col-span-2"
      actions={
        items.length > SHOWN ? (
          <Button size="sm" variant="ghost" onClick={() => setAll((current) => !current)}>
            {all ? t("server.attention.showFewer") : t("server.attention.showAll", { count: items.length })}
          </Button>
        ) : undefined
      }
    >
      {loading ? (
        <div aria-busy="true" className="flex flex-col gap-3 px-5 pb-4">
          <span className="sr-only">{t("server.attention.loading")}</span>
          {Array.from({ length: 3 }, (_, index) => (
            <Skeleton key={index} className="h-8 w-full" />
          ))}
        </div>
      ) : items.length === 0 ? (
        <p className="flex items-center gap-2 px-5 pb-4 text-13 text-fg-muted">
          <ICONS.success aria-hidden="true" className="size-icon-sm text-ok" />
          {t("server.attention.nothing")}
        </p>
      ) : (
        <ul aria-label={t("server.attention.listLabel")} className="flex flex-col divide-y divide-border">
          {shown.map((item) => (
            <AttentionRow key={item.id} item={item} />
          ))}
        </ul>
      )}
    </Card>
  );
}

function supportText(t: T, summary: ServerSummary): string {
  const eol = summary.os.eol;
  const date = day(eol.end_date, t);
  if (eol.status === "expired") return date !== null ? t("server.overview.supportEnded", { date }) : t("server.overview.supportEndedNoDate");
  if (eol.status === "warn" && eol.days_left != null) return t("server.overview.supportEnding", { count: eol.days_left, date: date ?? "" });
  if (eol.status === "ok" && date !== null) return t("server.overview.supportUntil", { date });
  return t("server.overview.supportUnknown");
}

function autoText(t: T, summary: ServerSummary): string {
  const auto = summary.auto_updates;
  if (auto.supported === false) return t("server.overview.autoUnavailable");
  if (auto.enabled === true) return auto.security_only === true ? t("server.overview.autoSecurity") : t("server.overview.autoAll");
  if (auto.enabled === false) return t("server.overview.autoOff");
  return t("server.overview.autoUnknown");
}

function Facts({ summary }: { summary: ServerSummary | undefined }) {
  const t = useT();
  if (summary === undefined) {
    return (
      <Card level={2} title={t("server.overview.factsTitle")} padding="sm">
        <div aria-busy="true">
          <span className="sr-only">{t("server.overview.loading")}</span>
          <KeyValueListSkeleton rows={6} />
        </div>
      </Card>
    );
  }
  const scheduled = summary.power.scheduled;
  const due = parseTimestamp(scheduled?.scheduled_for ?? null);
  const items: KeyValueItem[] = [
    { label: t("server.overview.hostname"), value: summary.hostname },
    {
      label: t("server.overview.system"),
      value: (
        <span className="flex min-w-0 flex-col">
          <span className="truncate text-13 text-fg">{summary.os.name}</span>
          <span className="truncate text-12 text-fg-muted">{supportText(t, summary)}</span>
        </span>
      ),
      mono: false,
      copy: false,
    },
    { label: t("server.overview.kernel"), value: summary.kernel },
    {
      label: t("server.overview.uptime"),
      value: summary.uptime_seconds != null ? formatDuration(summary.uptime_seconds, t.locale) : null,
      mono: false,
      copy: false,
    },
    { label: t("server.overview.autoUpdates"), value: autoText(t, summary), mono: false, copy: false },
    {
      label: t("server.overview.scheduled"),
      value:
        scheduled !== null && scheduled !== undefined ? (
          <span className="text-13 text-fg">
            {scheduled.action === "reboot" ? t("server.overview.scheduledReboot") : t("server.overview.scheduledShutdown")}{" "}
            {due ? <RelativeTime value={scheduled.scheduled_for} /> : <Mono>{scheduled.scheduled_for}</Mono>}
          </span>
        ) : (
          t("server.overview.nothingScheduled")
        ),
      mono: false,
      copy: false,
    },
  ];
  return (
    <Card level={2} title={t("server.overview.factsTitle")} padding="sm">
      <KeyValueList items={items} />
    </Card>
  );
}

function Readings({ summary, security }: { summary: ServerSummary | undefined; security: SecurityOverview | undefined }) {
  const t = useT();
  const machine = useQuery(machineQuery());
  const pending = <Skeleton className="h-5 w-16" />;
  const updates = summary?.updates;
  const counts = security?.counts ?? null;
  const disk = summary?.disk;
  const memory = machine.data?.memory;
  const time = summary?.time;
  const checkedAt = parseTimestamp(updates?.checked_at ?? null);
  return (
    <div className="grid min-w-0 grid-cols-2 gap-3 sm:grid-cols-3 xl:grid-cols-5">
      <StatTile
        label={t("server.overview.updates")}
        value={
          updates === undefined
            ? pending
            : !updates.supported
              ? t("server.overview.updatesUnmanaged")
              : updates.pending == null
                ? pending
                : updates.pending === 0
                  ? t("server.overview.upToDate")
                  : t("server.overview.updatesPending", { count: updates.pending })
        }
        detail={
          !updates?.supported
            ? (updates?.reason ?? undefined)
            : updates.security != null && updates.security > 0
              ? t("server.overview.updatesSecurity", { count: updates.security })
              : checkedAt
                ? t.rich("server.overview.updatesChecked", { when: <RelativeTime value={updates.checked_at ?? ""} /> })
                : undefined
        }
      />
      <StatTile
        label={t("server.overview.security")}
        value={security === undefined ? pending : counts === null ? t("server.overview.notChecked") : counts.critical + counts.warning === 0 ? t("server.overview.noFindings") : t("server.overview.findings", { count: counts.critical + counts.warning })}
        detail={counts !== null ? t("server.overview.findingsDetail", { critical: counts.critical, warning: counts.warning, passed: counts.passed }) : undefined}
      />
      <StatTile
        label={t("server.overview.disk")}
        value={disk === undefined ? pending : disk.worst_percent != null ? formatPercent(disk.worst_percent, t.locale) : t("server.overview.noReading")}
        detail={
          disk?.worst_mount != null ? (
            <>
              <Mono tone="faint">{disk.worst_mount}</Mono>
              {disk.free_bytes != null ? ` ${t("server.overview.diskFree", { free: formatBytes(disk.free_bytes, t.locale) })}` : ""}
            </>
          ) : undefined
        }
      />
      <StatTile
        label={t("server.overview.memory")}
        value={memory === undefined ? pending : formatPercent(memory.percent, t.locale)}
        detail={
          memory !== undefined
            ? t("server.overview.memoryDetail", {
                used: formatBytesPair(memory.used, memory.total, t.locale)[0],
                total: formatBytesPair(memory.used, memory.total, t.locale)[1],
                swap: summary?.swap.total_bytes != null ? formatBytes(summary.swap.total_bytes, t.locale) : "–",
              })
            : undefined
        }
      />
      <StatTile
        label={t("server.overview.clock")}
        value={
          time === undefined || (time.synchronized == null && time.error == null)
            ? pending
            : time.synchronized === true
              ? t("server.overview.clockSynced")
              : time.synchronized === false
                ? t("server.overview.clockUnsynced")
                : t("server.overview.noReading")
        }
        detail={time?.timezone != null ? <Mono tone="faint">{time.timezone}</Mono> : undefined}
        className="max-sm:col-span-2"
      />
    </div>
  );
}

function Overview() {
  const t = useT();
  const summary = useQuery(summaryQuery());
  const security = useQuery(securityOverviewQuery());
  const items = attentionItems(summary.data, security.data);
  return (
    <div className="flex min-w-0 flex-col gap-6">
      {summary.isError && summary.data === undefined ? (
        <ServerErrorBlock error={summary.error} title={t("server.overview.loadFailed")} onRetry={() => void summary.refetch()} retrying={summary.isRefetching} />
      ) : null}
      <div className="grid min-w-0 items-start gap-6 lg:grid-cols-3">
        <NeedsAttention items={items} loading={summary.data === undefined && !summary.isError} />
        <Facts summary={summary.data} />
      </div>
      <Readings summary={summary.data} security={security.data} />
      <CommandHint command="noust server status" label={t("server.fromTerminal")} />
    </div>
  );
}

/** The Overview tab. */
export function OverviewTab() {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      <Overview />
    </NodeCapabilityGate>
  );
}
