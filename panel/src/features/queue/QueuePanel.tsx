import { useQuery } from "@tanstack/react-query";
import type { UseQueryResult } from "@tanstack/react-query";

import { appsQuery } from "../../api/queries/apps";
import type { AppList } from "../../api/queries/apps";
import { queueQuery } from "../../api/queries/queue";
import type { Queue, QueueEntry } from "../../api/queries/queue";
import { deployStatus } from "../../components/page/status";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { Card } from "../../components/ui/Card";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { CancelJobButton } from "./CancelJobButton";
import { useCancelJob } from "./useCancelJob";
import type { CancelTarget } from "./useCancelJob";
import { cancelReasonWords, cancelState, entryDomain, entryTitle, isWaiting, mergedRequests, placeOf } from "./words";
import type { CancelState } from "./words";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

export interface QueuePanelProps {
  /** Only the jobs of this application: the Deployments tab's panel. Omitted, the server's. */
  domain?: string;
  /** Adds a "View log" to each row, which opens the job's log where this panel is used. */
  onOpenLog?: (entry: QueueEntry) => void;
}

/** The id the sentence that explains a disabled Cancel carries, so the button can point at it. */
function reasonIdOf(entry: Pick<QueueEntry, "id">): string {
  return `queue-reason-${entry.id}`;
}

/** What the job is and what holds it: what it waits for, what was merged into it, why it cannot stop. */
function JobCell({ entry, state, t }: { entry: QueueEntry; state: CancelState; t: T }) {
  const merged = mergedRequests(entry);
  return (
    <div className="flex min-w-0 flex-col gap-0.5">
      <span className="truncate font-medium text-fg" title={entryTitle(entry, t.locale)}>
        {entryTitle(entry, t.locale)}
      </span>
      {isWaiting(entry) && entry.waiting_for ? (
        // What it waits for is the queue's own phrase, as it came, set apart as the system's words.
        <span className="text-12 text-pretty text-fg-muted">{t.rich("queue.waitingForLine", { what: <Mono key="what">{entry.waiting_for}</Mono> })}</span>
      ) : null}
      {merged > 0 ? <span className="text-12 text-fg-muted">{t("queue.merged", { count: merged })}</span> : null}
      {state.kind === "stopping" ? <span className="text-12 text-pretty text-fg-muted">{t("queue.cancel.stoppingNote")}</span> : null}
      {state.kind === "cannot" ? (
        <span id={reasonIdOf(entry)} className="text-12 text-pretty text-fg-muted">
          {cancelReasonWords(state.reason, t.locale)}
        </span>
      ) : null}
    </div>
  );
}

function columns(t: T, apps: AppList["apps"] | undefined): Column<QueueEntry>[] {
  return [
    {
      id: "job",
      header: t("queue.columns.job"),
      cell: (entry) => <JobCell entry={entry} state={cancelState(entry, apps)} t={t} />,
      card: "title",
    },
    {
      id: "state",
      header: t("queue.columns.state"),
      cell: (entry) => {
        const view = deployStatus(entry.status, t.locale);
        // Asked to stop and still running: said as it is, in the working colour and shape.
        return entry.cancel_requested ? (
          <StatusPill state="deploying" label={t("queue.state.stopping")} appearance="inline" size="sm" />
        ) : (
          <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" />
        );
      },
      width: "w-36",
      card: "status",
    },
    {
      id: "place",
      header: t("queue.columns.place"),
      cell: (entry) => {
        const place = placeOf(entry);
        if (place !== null) {
          return <span className="tabular-nums">{place.next ? t("queue.place.next") : t("queue.place.position", { position: place.position })}</span>;
        }
        if (entry.started_at) return t.rich("queue.place.since", { time: <RelativeTime key="since" value={entry.started_at} /> });
        return <EmptyCell reason={t("queue.place.none")} />;
      },
      width: "w-52",
      card: "meta",
    },
  ];
}

/** The part of the queue's read the panel draws from: a page that decides whether to draw it reads it too. */
export type QueueRead = Pick<UseQueryResult<Queue>, "data" | "error" | "isError" | "isRefetching" | "refetch">;

/**
 * What builds and what waits, in the order it runs: the deploy queue, as the server keeps it.
 * Builds of different applications run side by side up to the server's limit and an
 * application builds one thing at a time, so what is asked meanwhile waits its turn here, with
 * its place and what it waits for in the server's own words, and requests for the same work
 * merged into one. A job that waits, and a build that has not started to activate its release,
 * can be cancelled from its row; anything else says why not. It draws nothing while the queue
 * is empty: the history under it is the page.
 *
 * Given the read, not making it: a page that draws the panel only when something is in the
 * queue reads it once itself, and a second reader that mounts when the first one fails would
 * start the read again, which flips the page back to "loading" and unmounts it.
 */
export function QueuePanelView({ queue, domain, onOpenLog }: QueuePanelProps & { queue: QueueRead }) {
  const t = useT();
  const apps = useQuery(appsQuery());
  const cancel = useCancelJob();

  if (queue.isError && queue.data === undefined) {
    return <ErrorBlock compact error={queue.error} title={t("queue.readFailed")} onRetry={() => void queue.refetch()} retrying={queue.isRefetching} />;
  }
  if (queue.data === undefined) return null;
  const data = queue.data;

  const mine = (entry: QueueEntry): boolean => domain === undefined || entryDomain(entry) === domain;
  const entries = [...data.running.filter(mine), ...data.queued.filter(mine)];
  if (entries.length === 0) return null;

  const ask = (target: CancelTarget): void => cancel.ask(target);
  return (
    <Card
      level={2}
      padding="none"
      title={domain === undefined ? t("queue.title") : t("queue.titleApp", { domain })}
      description={
        <span className="flex flex-col gap-1">
          <span>{domain === undefined ? t("queue.description") : t("queue.descriptionApp")}</span>
          {domain === undefined ? (
            <span className="flex flex-wrap gap-x-4 text-12 tabular-nums text-fg-muted">
              <span>{t("queue.limits.builds", { running: data.builds_running, max: data.max_concurrent_builds })}</span>
              <span>{t("queue.limits.waiting", { waiting: data.builds_queued, limit: data.queue_limit })}</span>
            </span>
          ) : null}
        </span>
      }
    >
      <DataTable
        caption={domain === undefined ? t("queue.caption") : t("queue.captionApp", { domain })}
        columns={columns(t, apps.data?.apps)}
        rows={entries}
        getRowId={(entry) => entry.id}
        density="compact"
        mobile="cards"
        rowActions={(entry) => (
          <span className="flex items-center gap-1">
            {onOpenLog !== undefined ? (
              <Button
                size="sm"
                variant="ghost"
                icon={<OBJECT_ICONS.log aria-hidden="true" />}
                aria-label={t("activity.viewLogAria", { name: entryTitle(entry, t.locale) })}
                onClick={() => onOpenLog(entry)}
              >
                {t("activity.viewLog")}
              </Button>
            ) : null}
            <CancelJobButton entry={entry} state={cancelState(entry, apps.data?.apps)} onAsk={ask} reasonId={reasonIdOf(entry)} />
          </span>
        )}
      />
      {cancel.dialog}
    </Card>
  );
}

/** The queue panel, reading the queue itself: for a page that always has a place for it. */
export function QueuePanel(props: QueuePanelProps) {
  const queue = useQuery(queueQuery());
  return <QueuePanelView queue={queue} {...props} />;
}
