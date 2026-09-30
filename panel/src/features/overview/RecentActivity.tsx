import { Link } from "@tanstack/react-router";

import type { OverviewActivityEntry } from "../../api/queries/overview";
import { useNow } from "../../components/page/clock";
import { deployStatus } from "../../components/page/status";
import { Card } from "../../components/ui/Card";
import { Mono } from "../../components/ui/Mono";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatClock, formatDate, formatDateTime } from "../../lib/format";
import { jobActionLabel } from "../activity/data";
import { activityDays } from "./overviewData";
import type { ActivityDay } from "./overviewData";

/** As many as sit beside five attention items at 1440 without making the row taller. */
export const ACTIVITY_SHOWN = 7;


function dayWords(t: T, day: ActivityDay["day"]): string {
  if (day === "today") return t("overview.activity.today");
  if (day === "yesterday") return t("overview.activity.yesterday");
  return formatDate(day, { year: false }, t.locale);
}

function Entry({ t, entry, at }: { t: T; entry: OverviewActivityEntry; at: Date | null }) {
  const view = deployStatus(entry.status, t.locale);
  // Succeeded is the expected ending: said by the glyph and for screen readers, not repeated in
  // words on every line. Anything else (failed, in progress, cancelled) is said out loud.
  const quiet = view.state === "running";
  const action = jobActionLabel(t, entry.type);
  const domain = entry.domain ?? null;
  return (
    <li className="flex min-w-0 items-center gap-2.5 py-1.5">
      <span className={cx("flex shrink-0", stateTextClass(view.state))}>
        <StatusGlyph state={view.state} size={12} />
      </span>
      <span className="flex min-w-0 flex-1 items-baseline gap-1.5 text-13">
        <span className="shrink-0 text-fg">{action}</span>
        {domain !== null ? (
          <Link to="/apps/$domain" params={{ domain }} className="min-w-0 truncate rounded-chip text-fg-muted hover:text-fg hover:underline">
            <Mono>{domain}</Mono>
          </Link>
        ) : (
          <Mono tone="muted" truncate>
            {entry.title}
          </Mono>
        )}
        <span className={cx("shrink-0 text-12", quiet ? "sr-only" : stateTextClass(view.state))}>{view.label}</span>
      </span>
      {at !== null ? (
        <time dateTime={at.toISOString()} title={formatDateTime(at, t.locale)} className="shrink-0 text-12 text-fg-faint tabular-nums">
          {formatClock(at, t.locale)}
        </time>
      ) : null}
    </li>
  );
}

export interface RecentActivityProps {
  entries: readonly OverviewActivityEntry[] | undefined;
}

/**
 * What happened lately, as a timeline by day: each job with its state as a glyph (and a word
 * when it did not simply succeed), what it did, on which application, and when.
 */
export function RecentActivity({ entries }: RecentActivityProps) {
  const t = useT();
  const now = useNow(() => 60_000);
  const days = entries === undefined ? [] : activityDays(entries, new Date(now), ACTIVITY_SHOWN);
  return (
    <Card
      as="section"
      level={2}
      padding="none"
      className="h-full"
      title={t("overview.activity.title")}
      actions={
        <TextLink to="/activity" size="ui">
          {t("overview.activity.all")}
        </TextLink>
      }
    >
      {entries === undefined ? (
        <div aria-busy="true" className="flex flex-col gap-3 px-4 py-3.5">
          <span className="sr-only">{t("overview.activity.title")}</span>
          {[0, 1, 2].map((row) => (
            <div key={row} aria-hidden="true" className="flex items-center gap-2.5">
              <Skeleton className="size-3 rounded-pill" />
              <Skeleton className="h-3.5 w-56 max-w-full" />
            </div>
          ))}
        </div>
      ) : days.length === 0 ? (
        <div className="px-4 py-3.5 text-13 text-pretty text-fg-muted">
          <span className="font-medium text-fg">{t("overview.activity.emptyTitle")}</span> {t("overview.activity.emptyDescription")}
        </div>
      ) : (
        <div className="flex flex-col gap-2 px-4 py-3" aria-label={t("overview.activity.listLabel")} role="group">
          {days.map((group) => {
            const label = dayWords(t, group.day);
            return (
              <div key={label} className="flex flex-col">
                <p className="text-12 font-medium text-fg-faint">{label}</p>
                <ol className="flex flex-col" aria-label={label}>
                  {group.entries.map(({ entry, at }) => (
                    <Entry key={entry.id} t={t} entry={entry} at={at} />
                  ))}
                </ol>
              </div>
            );
          })}
        </div>
      )}
    </Card>
  );
}
