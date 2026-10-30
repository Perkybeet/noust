import { Link } from "@tanstack/react-router";

import { LoadingRegion } from "../../components/page/LoadingRegion";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Section } from "../../components/page/Section";
import { deployStatus } from "../../components/page/status";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { RowList } from "../../components/ui/RowList";
import { SkeletonRows } from "../../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { TextLink } from "../../components/ui/TextLink";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { describeActor, jobActionLabel } from "../activity/data";
import type { ActivityLine } from "./homeData";

/** As many as sit beside six attention cards at 1440 without making the row taller. */
export const ACTIVITY_SHOWN = 6;

function Line({ t, line }: { t: T; line: ActivityLine }) {
  const { entry, at, server } = line;
  const view = deployStatus(entry.status, t.locale);
  // Succeeded is the expected ending: said by the glyph and for screen readers, not repeated in
  // words on every line. Anything else (failed, in progress, cancelled) is said out loud.
  const quiet = view.state === "running";
  const domain = entry.domain ?? null;
  const who = entry.actor !== null && entry.actor !== undefined && entry.actor !== "" ? describeActor(t, entry.actor).label : null;
  return (
    <RowList.Item leading={<StatusGlyph state={view.state} size={14} className={stateTextClass(view.state)} />}>
      <div className="flex min-w-0 items-baseline justify-between gap-3">
        <p className="flex min-w-0 items-baseline gap-1.5 text-13">
          {/* What a job with no application of its own did is the server's own title: the sentence, not its type. */}
          {domain !== null ? <span className="shrink-0 text-fg">{jobActionLabel(t, entry.type)}</span> : null}
          {domain !== null ? (
            <Link
              to="/apps/$domain"
              params={{ domain }}
              {...(line.node !== undefined ? { search: { node: line.node ?? undefined } as never } : {})}
              className="min-w-0 truncate rounded-chip text-fg-muted hover:text-fg hover:underline"
            >
              <span translate="no">{domain}</span>
            </Link>
          ) : (
            <span translate="no" className="min-w-0 truncate text-fg">
              {entry.title !== "" ? entry.title : jobActionLabel(t, entry.type)}
            </span>
          )}
          <span className={cx("shrink-0 text-12", quiet ? "sr-only" : stateTextClass(view.state))}>{view.label}</span>
        </p>
        {at !== null ? <RelativeTime value={at.toISOString()} className="shrink-0 text-12 text-fg-muted" /> : null}
      </div>
      {who !== null || server !== null ? (
        <p className="mt-0.5 flex min-w-0 flex-wrap items-baseline gap-x-3 text-12 text-fg-muted">
          {who !== null ? <span className="min-w-0 truncate">{who}</span> : null}
          {server !== null ? <Mono tone="muted">{server}</Mono> : null}
        </p>
      ) : null}
    </RowList.Item>
  );
}

export interface HomeActivityProps {
  /** What happened, newest first; undefined while it is read. */
  lines: readonly ActivityLine[] | undefined;
}

/** What happened lately on any server: each job with its state as a glyph, what it did, on which application, who did it and when. */
export function HomeActivity({ lines }: HomeActivityProps) {
  const t = useT();
  return (
    <Section
      title={t("overview.activity.title")}
      icon={OBJECT_ICONS.activity}
      loading={lines === undefined}
      actions={
        <TextLink to="/activity" size="ui">
          {t("overview.activity.all")}
        </TextLink>
      }
    >
      {lines === undefined ? (
        <LoadingRegion label={t("overview.activity.title")}>
          <SkeletonRows rows={4} />
        </LoadingRegion>
      ) : lines.length === 0 ? (
        <EmptyState variant="inline" title={t("overview.activity.emptyTitle")} description={t("overview.activity.emptyDescription")} />
      ) : (
        <RowList aria-label={t("overview.activity.listLabel")}>
          {lines.map((line) => (
            <Line key={line.key} t={t} line={line} />
          ))}
        </RowList>
      )}
    </Section>
  );
}
