import { useQuery } from "@tanstack/react-query";

import { centralScheduleQuery } from "../../api/queries/containerHost";
import type { ScheduledTask } from "../../api/queries/containerHost";
import { QueryState } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { calendarWords } from "../cron/data";

/** What systemd calls its own names for a calendar, as the expression each stands for: the words read the expression, so the name is said by it. */
const ALIASES: Readonly<Record<string, string>> = { hourly: "*-*-* *:00:00", daily: "*-*-* 00:00:00", weekly: "Mon *-*-* 00:00:00", monthly: "*-*-01 00:00:00" };

/** What a task is, in a sentence: its kind and what it is for. A kind this console does not know is said as the server named it. */
export function taskTitle(t: T, task: ScheduledTask): string {
  switch (task.kind) {
    case "backup":
    case "volume_backup":
      return t(`containerHost.schedule.kinds.${task.kind}`, { subject: task.subject });
    case "sweep":
    case "cleanup":
    case "image_check":
    case "resend":
      return t(`containerHost.schedule.kinds.${task.kind}`);
    default:
      return t("containerHost.schedule.kinds.other", { kind: task.kind });
  }
}

/** One task: what it is, when it runs, how its last run went, and what came due while Noust was off. */
function Task({ task }: { task: ScheduledTask }) {
  const t = useT();
  const failed = task.last_status === "failed";
  return (
    <li data-task={task.key} className="flex flex-col gap-1.5 px-5 py-3">
      <div className="flex flex-wrap items-baseline gap-x-3 gap-y-1">
        <span className="text-14 font-medium text-fg">{taskTitle(t, task)}</span>
        <span className="text-13 text-fg-muted">{calendarWords(task.calendar, ALIASES[task.calendar] ?? task.calendar, t.locale)}</span>
        {ALIASES[task.calendar] === undefined ? (
          <Mono tone="faint" className="text-12">
            {task.calendar}
          </Mono>
        ) : null}
      </div>
      <p className="flex flex-wrap items-center gap-x-4 gap-y-1 text-13 text-fg-muted">
        <span>{task.next_run !== null && task.next_run !== undefined ? t.rich("containerHost.schedule.nextAt", { when: <RelativeTime key="when" value={task.next_run} /> }) : t("containerHost.schedule.nextNone")}</span>
        <span>{task.last_run !== null && task.last_run !== undefined ? t.rich("containerHost.schedule.lastAt", { when: <RelativeTime key="when" value={task.last_run} /> }) : t("containerHost.schedule.never")}</span>
      </p>
      {failed ? (
        <p className="flex max-w-measure items-start gap-2 text-13 text-pretty text-fg">
          <StatusGlyph state="failed" size={12} className="mt-1 text-fail" />
          <span>{task.last_detail !== "" ? t("containerHost.schedule.failed", { detail: task.last_detail }) : t("containerHost.schedule.failedNoDetail")}</span>
        </p>
      ) : null}
      {task.missed > 0 ? <p className="max-w-measure text-13 text-pretty text-fg">{t("containerHost.schedule.missed", { count: task.missed })}</p> : null}
      {task.problem !== null && task.problem !== undefined ? (
        <p className="flex max-w-measure items-start gap-2 text-13 text-pretty text-fg">
          <StatusGlyph state="warning" size={12} className="mt-1 text-warn" />
          <span>{t("containerHost.schedule.problem", { problem: task.problem })}</span>
        </p>
      ) : null}
    </li>
  );
}

/**
 * What a central with no timers runs by itself: its backups, their retention sweep, the cleanup of Docker and, when it was asked to,
 * the look for new versions of the images. A timer is persistent, so after a stop it runs what came due; this says it did, once, so
 * that a copy that was not made while the machine was off is not a silence. It reads `GET /api/central/schedule` and draws
 * nothing of its own where that is not the machine's way (the page that holds it decides).
 */
export function CentralSchedule() {
  const t = useT();
  const tasks = useQuery(centralScheduleQuery());
  return (
    <SurfaceSection title={t("containerHost.schedule.title")} icon={OBJECT_ICONS.schedule} description={t("containerHost.schedule.description")} flush>
      <QueryState
        query={tasks}
        sentences={{ loading: t("containerHost.schedule.loading"), failed: t("containerHost.schedule.loadFailed"), refreshFailed: t("containerHost.schedule.refreshFailed") }}
        skeleton={
          <div className="px-5 pb-4">
            <Skeleton className="h-5 w-2/3" />
          </div>
        }
        isEmpty={(list) => list.length === 0}
        empty={
          <div className="px-5 pb-2">
            <EmptyState variant="inline" title={t("containerHost.schedule.empty")} />
          </div>
        }
      >
        {(list) => (
          <ul aria-label={t("containerHost.schedule.label")} className="m-0 flex list-none flex-col divide-y divide-border border-t border-border p-0">
            {list.map((task) => (
              <Task key={task.key} task={task} />
            ))}
          </ul>
        )}
      </QueryState>
    </SurfaceSection>
  );
}
