import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ChevronDown, Pin, PinOff } from "lucide-react";
import { useState } from "react";

import { RelativeTime } from "../../components/page/RelativeTime";
import { stateTextClass, StatusGlyph } from "../../components/ui/StatusPill";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Progress } from "../../components/ui/Progress";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { formatDuration } from "../../lib/format";
import { isActive, placeOf } from "./model";
import type { Me, Task } from "./model";
import { LOG_POLL_MS, taskLogTailQuery } from "./queries";
import { pin, unpin, useTaskState } from "./store";
import { stepOf, titleOf, viewOf, whoOf } from "./words";

/** How long a task ran, when it ended and began. */
function tookOf(task: Task): number | null {
  const began = task.startedAt ?? task.createdAt;
  return task.completedAt !== null && began !== null ? Math.max(0, (task.completedAt - began) / 1000) : null;
}

/** A task's last lines, asked for with the row's "Log": what a job wrote, verbatim and in mono. */
function LogTail({ task, title }: { task: Task; title: string }) {
  const t = useT();
  const log = useQuery({ ...taskLogTailQuery(task.server, task.id), refetchInterval: task.state === "running" ? LOG_POLL_MS : false });
  const content = log.data?.content ?? "";
  const words = log.isError ? t("tasks.row.logFailed") : log.data === undefined ? "" : content === "" ? t("tasks.row.logEmpty") : content;
  return (
    <SystemOutput label={t("tasks.row.logOf", { title })} maxHeight="max-h-28" className="mt-1.5 rounded-control bg-bg-sunken px-2.5 py-2">
      {words}
    </SystemOutput>
  );
}

export interface TaskRowProps {
  task: Task;
  /** Who the console is signed in as: what makes a task "yours". */
  me: Me;
  /** Names the server on the row: on "All servers" a task says where it runs. */
  showServer?: string | null;
  /** Called when a link of the row is followed, so the panel closes. */
  onNavigate: () => void;
}

/**
 * One task in the panel: what it is and how far it has got, who started it and when, its last lines of log when asked, a
 * way to pin it and a way to its page. A failed one says where it stopped and what it printed, verbatim.
 */
export function TaskRow({ task, me, showServer = null, onNavigate }: TaskRowProps) {
  const t = useT();
  const [open, setOpen] = useState(false);
  const { pinned } = useTaskState();
  const isPinned = pinned.includes(task.key);
  const title = titleOf(task, t.locale);
  const view = viewOf(task.state, t.locale);
  const step = stepOf(task, t.locale);
  const place = placeOf(task);
  const finished = !isActive(task);
  const took = finished ? tookOf(task) : null;
  const when = task.completedAt ?? task.startedAt ?? task.createdAt;
  const failed = task.state === "failed";
  return (
    <li className="flex flex-col gap-1.5 px-4 py-3">
      <div className="flex items-start gap-2.5">
        <span className={cx("mt-1", stateTextClass(view.state))}>
          <StatusGlyph state={view.state} />
          <span className="sr-only">{view.label}</span>
        </span>
        <div className="min-w-0 flex-1">
          <p className="truncate text-13 font-medium text-fg">{title}</p>
          {step !== null && step !== "" ? <p className={cx("truncate text-12", failed ? stateTextClass("failed") : "text-fg-muted")}>{task.state === "queued" ? t.rich("queue.waitingForLine", { what: <Mono key="what">{step}</Mono> }) : step}</p> : task.state === "queued" ? <p className="truncate text-12 text-fg-muted">{t("tasks.row.waiting")}</p> : null}
        </div>
        {task.state === "running" ? (
          <button
            type="button"
            aria-pressed={isPinned}
            aria-label={isPinned ? t("tasks.row.unpin", { title }) : t("tasks.row.pin", { title })}
            onClick={() => {
              if (isPinned) unpin(task.key);
              else pin(task.key);
            }}
            className={cx("flex size-7 shrink-0 cursor-pointer items-center justify-center rounded-control hover:bg-surface-hover", isPinned ? "text-accent-fg" : "text-fg-faint hover:text-fg")}
          >
            {isPinned ? <PinOff aria-hidden="true" className="size-icon-sm" /> : <Pin aria-hidden="true" className="size-icon-sm" />}
          </button>
        ) : null}
      </div>
      {task.state === "running" && task.progress !== null ? <Progress layout="inline" value={task.progress} label={title} className="pl-5" /> : null}
      {finished && failed && task.error !== null ? (
        <span className="ml-5 min-w-0">
          <Mono truncate className="text-12">
            {task.error.split("\n")[0] ?? ""}
          </Mono>
        </span>
      ) : null}
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1 pl-5 text-12 text-fg-muted">
        <span>{whoOf(task, me, t)}</span>
        {when !== null ? (
          <span>
            {took !== null
              ? t.rich("tasks.row.tookFor", { when: <RelativeTime key="when" value={when} />, duration: formatDuration(took, t.locale) })
              : <RelativeTime value={when} />}
          </span>
        ) : null}
        {showServer !== null ? (
          <span translate="no" className="rounded-chip bg-bg-sunken px-1.5 text-fg-muted">
            {showServer}
          </span>
        ) : null}
        <span className="ml-auto flex items-center gap-1">
          {task.kind === "job" ? (
            <button
              type="button"
              aria-expanded={open}
              onClick={() => {
                setOpen(!open);
              }}
              className="inline-flex cursor-pointer items-center gap-1 rounded-control px-1.5 py-0.5 font-medium text-fg-muted hover:bg-surface-hover hover:text-fg"
            >
              <OBJECT_ICONS.log aria-hidden="true" className="size-icon-xs" />
              {t("tasks.row.log")}
              <ChevronDown aria-hidden="true" className={cx("size-icon-xs transition-transform duration-(--duration-move) ease-(--ease-standard)", open && "rotate-180")} />
            </button>
          ) : null}
          <Link
            to={place.to as never}
            {...(place.params !== undefined ? { params: place.params as never } : {})}
            {...(place.search !== undefined ? { search: place.search as never } : {})}
            onClick={onNavigate}
            aria-label={t("tasks.row.openNamed", { title })}
            className="rounded-control px-1.5 py-0.5 font-medium text-accent-fg hover:bg-accent-soft"
          >
            {t("tasks.row.open")}
          </Link>
        </span>
      </div>
      {open ? <LogTail task={task} title={title} /> : null}
    </li>
  );
}
