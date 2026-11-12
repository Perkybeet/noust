import { useState } from "react";
import type { ReactNode } from "react";

import { LoadingRegion } from "../../components/page/LoadingRegion";
import { RelativeTime } from "../../components/page/RelativeTime";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
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
import { AttentionRows } from "./AttentionRows";
import { useAttention } from "./useAttention";

type View = "needs" | "recent";

function NeedsList({ t, onNavigate }: { t: T; onNavigate: () => void }) {
  const { items, loading, failed } = useAttention();
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
  return <AttentionRows t={t} items={items} label={t("inbox.needs.count", { count: items.length })} onNavigate={onNavigate} />;
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
  /**
   * The panel is the phone's sheet, which has the room and scrolls by itself: its list is not cut to a height. The popover's is,
   * and a list cut to a height inside a sheet hid the rest of its lines under a scroll nobody saw (item 144).
   */
  sheet?: boolean;
}

/**
 * What the inbox shows (docs/DESIGN.md 6.14, 6.12): "Needs you", a state - it is there while it is
 * true, whether or not the console was open when it happened - and "Recent", only what the server
 * said while this tab was open. The history is Activity, one link away, and the panel says so.
 */
export default function InboxPanel({ onNavigate, sheet = false }: InboxPanelProps): ReactNode {
  const t = useT();
  const [view, setView] = useState<View>("needs");
  const { total } = useAttention();
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
      <div className={cx(!sheet && "max-h-96 overflow-y-auto", "border-y border-border scroll-thin")}>
        {view === "needs" ? <NeedsList t={t} onNavigate={onNavigate} /> : <RecentList t={t} named={hasFleet} />}
      </div>
      <div className="flex items-center justify-between gap-3 px-4 text-12 text-fg-muted">
        <span>{view === "recent" ? t("inbox.recent.footer") : null}</span>
        <TextLink to="/activity" size="ui" onClick={onNavigate} className="inline-flex items-center gap-1.5">
          <OBJECT_ICONS.activity aria-hidden="true" className="size-icon-sm shrink-0" />
          {t("inbox.history")}
        </TextLink>
      </div>
    </div>
  );
}
