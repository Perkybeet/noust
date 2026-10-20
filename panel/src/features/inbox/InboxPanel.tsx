import { Link } from "@tanstack/react-router";
import { useState } from "react";
import type { ReactNode } from "react";

import type { OverviewAttentionItem } from "../../api/queries/overview";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { RelativeTime } from "../../components/page/RelativeTime";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { useHasFleet } from "../../nodes/servers";
import { useNode } from "../../nodes/useNode";
import { useRecentNotices } from "../../realtime/notices";
import type { NoticeState, RecentNotice } from "../../realtime/notices";
import { SeverityGlyph, reasonText, validUntilText } from "../overview/NeedsAttention";
import { attentionTarget } from "../overview/attentionTarget";
import { AttentionActionButton } from "./AttentionActionButton";
import { useAttention } from "./useAttention";

type View = "needs" | "recent";

/** One item that needs the operator: what it is about and what is wrong, a click on either opens the screen that deals with it, and one button does the first thing there. */
function NeedsRow({ t, item, onNavigate }: { t: T; item: OverviewAttentionItem; onNavigate: () => void }) {
  const target = attentionTarget(item);
  const when = item.reasons.find((reason) => reason.when)?.when ?? null;
  const severity = item.severity === "fail" ? "fail" : "warn";
  // The system's own words come from whichever reason has them: a service that failed has none, the deploy that left it so does.
  const reason = item.reasons.find((candidate) => typeof candidate.detail === "string" && candidate.detail !== "") ?? item.reasons[0];
  const detail = reason?.detail ?? (typeof reason?.params?.["valid_until"] === "string" ? validUntilText(t, reason.params["valid_until"]) : null);
  const kind = item.subject["kind"];
  const body = (
    <>
      <span className="flex min-w-0 items-baseline gap-2">
        <span className="min-w-0 truncate text-13 font-medium text-fg group-hover:underline group-hover:underline-offset-2">{kind === "alert" || kind === "probe" || kind === "recipe" || kind === "unit" ? <Mono tone="default">{item.title}</Mono> : <span translate="no">{item.title}</span>}</span>
        {when ? <RelativeTime value={when} className="shrink-0 text-12 text-fg-faint" /> : null}
      </span>
      {item.reasons.slice(0, 2).map((each, index) => (
        <span key={`${each.code}-${String(index)}`} className={cx("text-13 text-pretty", each.severity === "fail" ? "text-fg" : "text-fg-muted")}>
          {reasonText(t, each)}
        </span>
      ))}
      {typeof detail === "string" && detail !== "" ? (
        // The system's words, verbatim and wrapped, as everywhere.
        <Mono tone="muted" className="line-clamp-2 text-12 break-words whitespace-pre-wrap">
          {detail}
        </Mono>
      ) : null}
    </>
  );
  return (
    <li data-severity={severity} className="flex min-w-0 gap-3 px-4 py-3">
      <span className="flex h-5 shrink-0 items-center">
        <SeverityGlyph severity={severity} />
        <span className="sr-only">{severity === "fail" ? t("overview.attention.srFailure") : t("overview.attention.srWarning")}</span>
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-2">
        {target === null ? (
          <div className="flex min-w-0 flex-col gap-0.5">{body}</div>
        ) : (
          <Link to={target.to as never} {...(target.params ? { params: target.params as never } : {})} {...(target.search ? { search: target.search as never } : {})} onClick={onNavigate} className="group flex min-w-0 flex-col gap-0.5 rounded-chip">
            {body}
          </Link>
        )}
        <div className="flex justify-end">
          <AttentionActionButton item={item} onNavigate={onNavigate} />
        </div>
      </div>
    </li>
  );
}

function NeedsList({ t, onNavigate }: { t: T; onNavigate: () => void }) {
  const { items, loading, failed } = useAttention(true);
  if (loading) {
    return (
      <div className="px-4 py-3">
        <LoadingRegion label={t("inbox.needs.checking")}>
          {[0, 1].map((row) => (
            <div key={row} aria-hidden="true" className="flex gap-3 pb-3 last:pb-0">
              <Skeleton className="mt-0.5 size-3.5 rounded-pill" />
              <div className="flex flex-1 flex-col gap-2">
                <Skeleton className="h-3.5 w-40" />
                <Skeleton className="h-3 w-56 max-w-full" />
              </div>
            </div>
          ))}
        </LoadingRegion>
      </div>
    );
  }
  if (failed && items.length === 0) return <p className="px-4 py-3 text-13 text-fg-muted">{t("inbox.needs.couldNotLoad")}</p>;
  if (items.length === 0) {
    return (
      <div className="flex items-start gap-3 px-4 py-3">
        <span className="flex h-5 items-center">
          <StatusGlyph state="running" size={14} className="text-ok" />
        </span>
        <p className="text-13 text-pretty text-fg-muted">
          <span className="font-medium text-fg">{t("inbox.needs.emptyTitle")}</span> {t("inbox.needs.emptyDescription")}
        </p>
      </div>
    );
  }
  return (
    <ul aria-label={t("inbox.needs.count", { count: items.length })} className="divide-y divide-border">
      {items.map((item) => (
        <NeedsRow key={item.id} t={t} item={item} onNavigate={onNavigate} />
      ))}
    </ul>
  );
}

const NOTICE_STATE: Readonly<Record<NoticeState, { state: Status; word: "busy" | "active" | "failed" | "idle" }>> = {
  busy: { state: "deploying", word: "busy" },
  active: { state: "running", word: "active" },
  failed: { state: "failed", word: "failed" },
  idle: { state: "unknown", word: "idle" },
};

function RecentRow({ t, notice, named }: { t: T; notice: RecentNotice; named: boolean }) {
  const view = NOTICE_STATE[notice.state];
  return (
    <li className="flex min-w-0 gap-3 px-4 py-3">
      <span className="flex h-5 shrink-0 items-center">
        <StatusGlyph state={view.state} size={14} />
        <span className="sr-only">{t(`inbox.recent.${view.word}`)}: </span>
      </span>
      <div className="flex min-w-0 flex-1 flex-col gap-0.5">
        {/* The server's own words, as it said them. */}
        <span className="text-13 text-pretty break-words text-fg">{notice.text}</span>
        <span className="flex min-w-0 items-baseline gap-2 text-12 text-fg-faint">
          <RelativeTime value={notice.at} />
          {named ? <Mono tone="faint">{notice.node ?? ""}</Mono> : null}
        </span>
      </div>
    </li>
  );
}

function RecentList({ t, named }: { t: T; named: boolean }) {
  const notices = useRecentNotices();
  if (notices.length === 0) {
    return (
      <p className="px-4 py-3 text-13 text-pretty text-fg-muted">
        <span className="font-medium text-fg">{t("inbox.recent.emptyTitle")}</span> {t("inbox.recent.emptyDescription")}
      </p>
    );
  }
  return (
    <ul aria-label={t("inbox.tabs.recent")} className="divide-y divide-border">
      {notices.map((notice) => (
        <RecentRow key={notice.id} t={t} notice={notice} named={named && notice.node !== null} />
      ))}
    </ul>
  );
}

export interface InboxPanelProps {
  /** Called when the operator follows a link, so the popover or the drawer closes. */
  onNavigate: () => void;
}

/**
 * What the inbox shows (docs/DESIGN.md 6.14, 6.12): "Needs you", a state - it is there while it is
 * true, whether or not the console was open when it happened - and "Recent", only what the server
 * said while this tab was open. The history is Activity, one link away, and the panel says so.
 */
export default function InboxPanel({ onNavigate }: InboxPanelProps): ReactNode {
  const t = useT();
  const [view, setView] = useState<View>("needs");
  const { total } = useAttention(true);
  const { node } = useNode();
  const hasFleet = useHasFleet();
  return (
    <div className="-mx-4 flex flex-col gap-3">
      <div className="flex flex-col gap-2 px-4">
        <SegmentedControl<View>
          label={t("inbox.tabs.label")}
          value={view}
          onValueChange={setView}
          options={[
            { value: "needs", label: total > 0 ? t("inbox.tabs.needs", { count: total }) : t("inbox.tabs.needsNone") },
            { value: "recent", label: t("inbox.tabs.recent") },
          ]}
        />
        {hasFleet ? <p className="text-12 text-fg-muted">{t.rich("inbox.forServer", { name: <Mono key="name">{node ?? t("fleet.selector.thisServer")}</Mono> })}</p> : null}
      </div>
      <div className="max-h-96 overflow-y-auto border-y border-border scroll-thin">
        {view === "needs" ? <NeedsList t={t} onNavigate={onNavigate} /> : <RecentList t={t} named={hasFleet} />}
      </div>
      <div className="flex items-center justify-between gap-3 px-4 text-12 text-fg-muted">
        <span>{view === "recent" ? t("inbox.recent.footer") : null}</span>
        <TextLink to="/activity" size="ui" onClick={onNavigate}>
          {t("inbox.history")}
        </TextLink>
      </div>
    </div>
  );
}
