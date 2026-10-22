import { Link } from "@tanstack/react-router";
import { useMemo, useState } from "react";
import type { ReactNode } from "react";

import type { OverviewAttentionItem, OverviewAttentionReason } from "../../api/queries/overview";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { RelativeTime } from "../../components/page/RelativeTime";
import { useAnnounceChange } from "../../components/page/useAnnounceChange";
import { Badge } from "../../components/ui/Badge";
import { Button, buttonClassName } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatDate, parseTimestamp } from "../../lib/format";
import { AttentionActionButton } from "../inbox/AttentionActionButton";
import { useExtraAttention } from "../inbox/useAttention";
import { mergeAttention } from "./attention";
import type { Severity } from "./attention";
import { attentionTarget } from "./attentionTarget";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/**
 * What the monitor saw, in words, by its signal (`src/noust/monitor/models.py`) and severity
 * (a warning asks more firmly than a notice); a signal this console does not know, by name.
 */
function monitorText(t: T, severity: string, signal: string): string {
  const warning = severity === "warning";
  switch (signal) {
    case "name-pattern":
      return warning ? t("overview.attention.monitor.nameWarning") : t("overview.attention.monitor.nameNotice");
    case "resource-usage":
      return warning ? t("overview.attention.monitor.usageWarning") : t("overview.attention.monitor.usageNotice");
    default:
      return t("overview.attention.monitor.other", { signal });
  }
}

/** A certificate's expiry, translated and in the console's date format. */
export function validUntilText(t: T, value: string): string {
  const date = parseTimestamp(value);
  return t("overview.attention.certValidUntil", { date: date ? formatDate(date, {}, t.locale) : value });
}

export function SeverityGlyph({ severity }: { severity: Severity }) {
  return <StatusGlyph state={severity === "fail" ? "failed" : "warning"} size={14} className={severity === "fail" ? "text-fail" : "text-warn"} />;
}

function text(value: unknown): string | null {
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

/** What the server says is wrong, as a sentence of the console's; a code it does not know, verbatim. */
export function reasonText(t: T, reason: OverviewAttentionReason): string {
  const params = reason.params ?? {};
  switch (reason.code) {
    case "service_failed":
      return t("overview.attention.serviceFailed");
    case "service_restarting":
      return t("overview.attention.serviceRestarting");
    case "running_outside_unit":
      return t("overview.attention.runningOutsideUnit");
    case "no_answer":
      return t("overview.attention.noAnswer");
    case "deploy_failed":
      return t("overview.attention.deployFailed");
    case "deploy_rolled_back":
      return t("overview.attention.deployRolledBack");
    case "certificate_expired":
      return t("overview.attention.certExpired");
    case "certificate_expires_today":
      return t("overview.attention.certExpiresToday");
    case "certificate_expires_in":
      return t("overview.attention.certExpiresIn", { count: Number(params["days"] ?? 0) });
    case "unit_failed":
      return t("overview.attention.unitFailed");
    case "unit_restarting":
      return t("overview.attention.unitRestarting");
    case "monitor_finding":
      return monitorText(t, text(params["severity"]) ?? "", text(params["signal"]) ?? "");
    // What the console adds to the server's list (features/overview/attention.ts).
    case "alert_firing":
      return t("inbox.reasons.alertFiring");
    case "alert_taken":
      return t("inbox.reasons.alertTaken", { by: text(params["by"]) ?? "" });
    case "probe_down":
      return t("inbox.reasons.probeDown");
    case "approval_waiting":
      return t("inbox.reasons.approvalWaiting", { by: text(params["by"]) ?? "" });
    case "noust_update":
      return t("inbox.reasons.noustUpdate", { version: text(params["version"]) ?? "", current: text(params["current"]) ?? "" });
    case "noust_security_update":
      return t("inbox.reasons.noustSecurityUpdate", { version: text(params["version"]) ?? "", current: text(params["current"]) ?? "" });
    case "recipe_update":
      return t("inbox.reasons.recipeUpdate", { recipe: text(params["recipe"]) ?? "", version: text(params["version"]) ?? "", current: text(params["current"]) ?? "" });
    default:
      return reason.code;
  }
}

/** The item's title, linking to the page that owns what it is about. */
function Subject({ t, item }: { t: T; item: OverviewAttentionItem }) {
  const kind = item.subject["kind"];
  const domain = text(item.subject["domain"]);
  const name = text(item.subject["name"]);
  if (kind === "app" && domain !== null) {
    return (
      <TextLink variant="row" size="ui" className="min-w-0 truncate" to="/apps/$domain" params={{ domain }}>
        <span translate="no">{item.title}</span>
      </TextLink>
    );
  }
  if (kind === "certificate") {
    // The certificates page filtered to this one, not the whole list to search through.
    return (
      <TextLink variant="row" size="ui" className="min-w-0 truncate" to="/domains" search={domain !== null ? { q: domain } : {}}>
        <span translate="no">{item.title}</span>
      </TextLink>
    );
  }
  if (kind === "unit" && name !== null) {
    return (
      <TextLink variant="row" size="ui" className="min-w-0 truncate" to="/services/$name" params={{ name }}>
        <Mono>{item.title}</Mono>
      </TextLink>
    );
  }
  if (kind === "alert" || kind === "probe" || kind === "approval" || kind === "noust" || kind === "recipe") {
    // What the console adds to the server's list: the screen where it is dealt with (attentionTarget).
    const target = attentionTarget(item);
    const label = kind === "alert" || kind === "probe" || kind === "recipe" ? <Mono>{item.title}</Mono> : <span translate="no">{item.title}</span>;
    return target === null ? (
      label
    ) : (
      <TextLink variant="row" size="ui" className="min-w-0 truncate" to={target.to as never}>
        {label}
      </TextLink>
    );
  }
  const pid = item.subject["pid"];
  return (
    <TextLink variant="row" size="ui" className="min-w-0 truncate" to="/server">
      <span translate="no">{item.title}</span>
      {typeof pid === "number" ? (
        <Mono tone="faint" className="ml-1.5 font-normal">
          {t("overview.attention.pid", { pid: String(pid) })}
        </Mono>
      ) : null}
    </TextLink>
  );
}

const ACTION = buttonClassName("secondary", "sm");

/** The one or two things to do about an item, where they are done: never more than two. */
function Actions({ t, item }: { t: T; item: OverviewAttentionItem }) {
  const kind = item.subject["kind"];
  if (kind === "alert" || kind === "probe" || kind === "approval" || kind === "noust" || kind === "recipe") {
    return (
      <div className="flex shrink-0 flex-wrap items-center gap-2">
        <AttentionActionButton item={item} />
      </div>
    );
  }
  const domain = text(item.subject["domain"]);
  const name = text(item.subject["name"]);
  const wanted = new Set(item.reasons.flatMap((reason) => reason.actions ?? []));
  const deploymentId = item.reasons.find((reason) => reason.deployment_id !== null && reason.deployment_id !== undefined)?.deployment_id ?? null;
  const out: ReactNode[] = [];
  if (domain !== null && item.subject["kind"] === "app") {
    if (wanted.has("view_deployment") && deploymentId !== null) {
      out.push(
        <Link
          key="deploy"
          to="/apps/$domain/deployments/$id"
          params={{ domain, id: String(deploymentId) }}
          aria-label={t("overview.attention.viewLogAria", { domain })}
          className={ACTION}
        >
          {t("overview.attention.viewLog")}
        </Link>,
      );
    } else if (wanted.has("view_log")) {
      out.push(
        <Link key="logs" to="/apps/$domain/logs" params={{ domain }} aria-label={t("overview.attention.appLogAria", { domain })} className={ACTION}>
          {t("overview.attention.appLog")}
        </Link>,
      );
    }
    if (wanted.has("diagnose")) {
      out.push(
        <Link key="diagnose" to="/apps/$domain/diagnose" params={{ domain }} aria-label={t("overview.attention.diagnoseAria", { domain })} className={ACTION}>
          {t("overview.attention.diagnose")}
        </Link>,
      );
    }
    if (wanted.has("renew_certificate") && out.length < 2) {
      out.push(
        <Link key="cert" to="/apps/$domain/domains" params={{ domain }} aria-label={t("overview.attention.certificateAria", { domain })} className={ACTION}>
          {t("overview.attention.certificate")}
        </Link>,
      );
    }
  } else if (item.subject["kind"] === "certificate" && domain !== null) {
    out.push(
      <Link key="cert" to="/domains" search={{ q: domain }} aria-label={t("overview.attention.certificateAria", { domain })} className={ACTION}>
        {t("overview.attention.certificate")}
      </Link>,
    );
  } else if (item.subject["kind"] === "unit" && name !== null) {
    out.push(
      <Link key="service" to="/services/$name" params={{ name }} aria-label={t("overview.attention.openServiceAria", { name })} className={ACTION}>
        {t("overview.attention.openService")}
      </Link>,
    );
  } else if (item.subject["kind"] === "monitor") {
    out.push(
      <Link key="finding" to="/server" aria-label={t("overview.attention.openFindingAria", { name: item.title })} className={ACTION}>
        {t("overview.attention.openFinding")}
      </Link>,
    );
  }
  if (out.length === 0) return null;
  return <div className="flex shrink-0 flex-wrap items-center gap-2">{out.slice(0, 2)}</div>;
}

function Item({ t, item }: { t: T; item: OverviewAttentionItem }) {
  const severity: Severity = item.severity === "fail" ? "fail" : "warn";
  const when = item.reasons.find((reason) => reason.when)?.when ?? null;
  return (
    <li data-severity={severity} className="flex min-w-0 gap-3 px-4 py-3">
      <span className="flex h-5 shrink-0 items-center">
        <SeverityGlyph severity={severity} />
        <span className="sr-only">{severity === "fail" ? t("overview.attention.srFailure") : t("overview.attention.srWarning")}</span>
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2 sm:flex-row sm:items-start sm:justify-between sm:gap-4">
        <div className="flex min-w-0 flex-col gap-0.5">
          <div className="flex min-w-0 items-baseline gap-2">
            <Subject t={t} item={item} />
            {when ? <RelativeTime value={when} className="shrink-0 text-12 text-fg-faint" /> : null}
          </div>
          <ul className="flex min-w-0 flex-col gap-0.5">
            {item.reasons.map((reason, index) => {
              const validUntil = text(reason.params?.["valid_until"]);
              const detail = reason.detail ?? (validUntil !== null ? validUntilText(t, validUntil) : null);
              return (
                <li key={`${reason.code}-${String(index)}`} className="flex min-w-0 flex-col">
                  <span className={cx("text-13", reason.severity === "fail" ? "text-fg" : "text-fg-muted")}>{reasonText(t, reason)}</span>
                  {detail !== null ? (
                    // The system's words wrap rather than being cut, on a phone too.
                    <Mono tone="muted" className="text-12 break-words whitespace-pre-wrap">
                      {detail}
                    </Mono>
                  ) : null}
                </li>
              );
            })}
          </ul>
        </div>
        <Actions t={t} item={item} />
      </div>
    </li>
  );
}

/** How many items show before "Show all": the worst five fit beside the activity at 1440. */
export const ATTENTION_SHOWN = 5;

export interface NeedsAttentionProps {
  items: readonly OverviewAttentionItem[] | undefined;
  total: number;
}

/**
 * What needs an operator, worst first, each with where it is fixed. Five at most until asked for
 * all; when nothing does, one calm line that says what was checked.
 */
export function NeedsAttention({ items: listed, total: listedTotal }: NeedsAttentionProps) {
  const t = useT();
  const [all, setAll] = useState(false);
  // The server's list and what the console adds - an alert firing, a probe down, a request waiting -
  // are the one list the inbox and the sidebar's counters show too.
  const extras = useExtraAttention();
  const items = useMemo(() => (listed === undefined ? undefined : mergeAttention(listed, extras)), [listed, extras]);
  const total = listedTotal + extras.length;
  const count = items === undefined ? null : total;
  useAnnounceChange(
    count === null ? null : String(count),
    count === null ? null : count === 0 ? t("overview.attention.announceEmpty") : t("overview.attention.announceCount", { count }),
    items?.some((item) => item.severity === "fail") === true ? "assertive" : "polite",
  );
  const worst = items?.[0]?.severity;
  const shown = items === undefined ? [] : all ? items : items.slice(0, ATTENTION_SHOWN);

  let body: ReactNode;
  if (items === undefined) {
    body = (
      <LoadingRegion label={t("overview.attention.checking")} className="divide-y divide-border">
        {[0, 1].map((row) => (
          <div key={row} aria-hidden="true" className="flex gap-3 px-4 py-3.5">
            <Skeleton className="mt-0.5 size-3.5 rounded-pill" />
            <div className="flex flex-1 flex-col gap-2">
              <Skeleton className="h-3.5 w-48" />
              <Skeleton className="h-3 w-72 max-w-full" />
            </div>
          </div>
        ))}
      </LoadingRegion>
    );
  } else if (items.length === 0) {
    body = (
      <div className="flex items-start gap-3 px-4 py-3.5">
        <span className="flex h-5 items-center">
          <StatusGlyph state="running" size={14} className="text-ok" />
        </span>
        <p className="text-13 text-pretty text-fg-muted">
          <span className="font-medium text-fg">{t("overview.attention.emptyTitle")}</span> {t("overview.attention.emptyDescription")}
        </p>
      </div>
    );
  } else {
    body = (
      <ul className="divide-y divide-border">
        {shown.map((item) => (
          <Item key={item.id} t={t} item={item} />
        ))}
      </ul>
    );
  }

  const more = items !== undefined && items.length > ATTENTION_SHOWN;
  return (
    <section aria-label={t("overview.attention.title")} className="h-full min-w-0">
      <Card
        as="div"
        level={2}
        padding="none"
        className="h-full"
        title={
          <span className="flex items-center gap-2">
            {t("overview.attention.title")}
            {count !== null && count > 0 ? (
              <span className="inline-flex items-center gap-1.5">
                <StatusGlyph state={worst === "fail" ? "failed" : "warning"} size={12} />
                <Badge>
                  <span className="sr-only">{t("overview.attention.itemsSr")}</span>
                  {count}
                </Badge>
              </span>
            ) : null}
          </span>
        } icon={OBJECT_ICONS.alert}
        // "Show all" in the card's header, as every card that folds a list has it.
        {...(more
          ? {
              actions: (
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setAll((value) => !value);
                  }}
                >
                  {all ? t("overview.attention.showFewer") : t("overview.attention.showAll", { count: items.length })}
                </Button>
              ),
            }
          : {})}
      >
        {body}
      </Card>
    </section>
  );
}
