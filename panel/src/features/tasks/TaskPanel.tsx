import { Link } from "@tanstack/react-router";
import type { ReactNode } from "react";
import { useId } from "react";

import { LoadingRegion } from "../../components/page/LoadingRegion";
import { useNow } from "../../components/page/clock";
import { Mono } from "../../components/ui/Mono";
import { VERB_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { isActive, isMine, sectionsOf } from "./model";
import type { Me, ServerKey, Task } from "./model";
import { TaskRow } from "./TaskRow";
import type { ServerStatus, TaskState } from "./store";
import { stepOf, titleOf, viewOf } from "./words";
import { stateTextClass, StatusGlyph } from "../../components/ui/StatusPill";

/** A titled band of the panel, with how many it holds. */
function Group({ title, count, note, children }: { title: string; count: number; note?: string; children?: ReactNode }) {
  const id = useId();
  return (
    <section aria-labelledby={id}>
      <p id={id} className="flex items-center gap-2 border-t border-border bg-bg-sunken px-4 py-1.5 text-12 font-medium text-fg-muted">
        {title}
        <Mono tone="muted" className="rounded-chip bg-surface px-1 tabular-nums">
          {count}
        </Mono>
      </p>
      {note !== undefined ? <p className="px-4 pt-2 text-12 text-fg-muted">{note}</p> : null}
      {children !== undefined ? <ul className="divide-y divide-border">{children}</ul> : null}
    </section>
  );
}

/** A task on one line, for a server's group on "All servers". */
function CompactRow({ task, onNavigate }: { task: Task; onNavigate: () => void }) {
  const t = useT();
  const title = titleOf(task, t.locale);
  const view = viewOf(task.state, t.locale);
  const step = stepOf(task, t.locale);
  return (
    <li>
      <Link
        to={task.kind === "job" ? "/activity/tasks/$id" : "/activity/tasks"}
        {...(task.kind === "job" ? { params: { id: task.id } } : {})}
        {...(task.server !== null ? { search: { node: task.server } as never } : {})}
        onClick={onNavigate}
        className="flex items-center gap-2.5 px-4 py-2 hover:bg-surface-hover"
      >
        <span className={stateTextClass(view.state)}>
          <StatusGlyph state={view.state} />
          <span className="sr-only">{view.label}</span>
        </span>
        <span className="min-w-0 flex-1">
          <span className="block truncate text-13 font-medium text-fg">{title}</span>
          {step !== null && step !== "" ? <span className="block truncate text-12 text-fg-muted">{step}</span> : null}
        </span>
        {task.progress !== null ? <span className="text-12 text-fg-muted tabular-nums">{task.progress} %</span> : null}
      </Link>
    </li>
  );
}

export interface ServerPanelProps {
  /** The tasks of the one server on screen. */
  tasks: readonly Task[];
  me: Me;
  onNavigate: () => void;
}

/** One server's panel: yours first, then what the server does that is not yours, then what ended lately. */
export function ServerPanel({ tasks, me, onNavigate }: ServerPanelProps) {
  const t = useT();
  const now = useNow(() => 60_000);
  const sections = sectionsOf(tasks, (task) => isMine(task, me), now);
  const row = (task: Task) => <TaskRow key={task.key} task={task} me={me} onNavigate={onNavigate} />;
  const nothing = sections.mineRunning.length + sections.mineQueued.length + sections.others.length + sections.done.length === 0;
  if (nothing) {
    return (
      <div className="border-t border-border px-4 py-4">
        <p className="text-13 text-fg">{t("tasks.empty")}</p>
        <p className="mt-1 text-12 text-fg-muted">{t("tasks.emptyHelp")}</p>
      </div>
    );
  }
  return (
    <>
      {sections.mineRunning.length > 0 ? <Group title={t("tasks.groups.mineRunning")} count={sections.mineRunning.length}>{sections.mineRunning.map(row)}</Group> : null}
      {sections.mineQueued.length > 0 ? <Group title={t("tasks.groups.mineQueued")} count={sections.mineQueued.length}>{sections.mineQueued.map(row)}</Group> : null}
      {sections.others.length > 0 ? (
        <Group title={t("tasks.groups.others")} count={sections.others.length} note={t("tasks.groups.othersHelp")}>
          {sections.others.map(row)}
        </Group>
      ) : null}
      {sections.done.length > 0 ? <Group title={t("tasks.groups.done")} count={sections.done.length}>{sections.done.map(row)}</Group> : null}
    </>
  );
}

export interface FleetPanelProps {
  state: TaskState;
  /** This server's version, which a server still on an older one is asked to reach. */
  target: string | null;
  onNavigate: () => void;
}

function serverKeyOf(id: string): ServerKey {
  return id === "" ? null : id;
}

/** "All servers": what each server runs, grouped by server, with how each answered. A server that cannot tell says why. */
export function FleetPanel({ state, target, onNavigate }: FleetPanelProps) {
  const t = useT();
  // What a server that predates the task centre is to be updated to: this central's own version.
  const goal = target ?? "4.2";
  const servers: [string, ServerStatus][] = [...state.servers.entries()];
  if (servers.length === 0) {
    return (
      <div className="border-t border-border px-4 py-3">
        <LoadingRegion label={t("tasks.title")}>
          {[0, 1].map((row) => (
            <div key={row} aria-hidden="true" className="flex flex-col gap-2 pb-2">
              <Skeleton className="h-3.5 w-40" />
              <Skeleton className="h-3 w-56 max-w-full" />
            </div>
          ))}
        </LoadingRegion>
      </div>
    );
  }
  return (
    <>
      {servers.map(([id, server]) => {
        const key = serverKeyOf(id);
        const running = [...state.tasks.values()].filter((task) => task.server === key && isActive(task));
        const answered = server.answer === "ok" || server.answer === "stale";
        return (
          <section key={id} aria-label={server.name}>
            <p className="flex items-center gap-2 border-t border-border bg-bg-sunken px-4 py-1.5 text-12 font-medium text-fg">
              <Mono tone="default">{server.name}</Mono>
              {key === null ? <span className="rounded-chip bg-surface px-1 text-fg-muted">{t("tasks.fleet.central")}</span> : null}
              {answered ? (
                <Mono tone="muted" className={cx("ml-auto rounded-chip bg-surface px-1 tabular-nums")}>
                  {running.length}
                </Mono>
              ) : null}
            </p>
            {server.answer === "unsupported" ? (
              <div className="flex items-start gap-2.5 px-4 py-3 text-12 text-fg-muted">
                <VERB_ICONS.refresh aria-hidden="true" className="mt-0.5 size-icon-sm shrink-0 text-fg-faint" />
                <p className="min-w-0 flex-1">
                  {server.version === null
                    ? t("tasks.fleet.unsupportedNoVersion", { target: goal })
                    : t("tasks.fleet.unsupported", { version: server.version, target: goal })}{" "}
                  <Link to="/fleet/updates" onClick={onNavigate} className="font-medium text-accent-fg">
                    {t("tasks.fleet.update")}
                  </Link>
                </p>
              </div>
            ) : server.answer === "unreachable" ? (
              <p className="flex items-center gap-2 px-4 py-3 text-12 text-fg-muted">
                <StatusGlyph state="failed" className={stateTextClass("failed")} />
                {t("tasks.fleet.unreachable")}
              </p>
            ) : server.answer === "forbidden" ? (
              <p className="px-4 py-3 text-12 text-fg-muted">{t("tasks.fleet.forbidden")}</p>
            ) : server.answer === "error" ? (
              <p className="flex items-center gap-2 px-4 py-3 text-12 text-fg-muted">
                <StatusGlyph state="failed" className={stateTextClass("failed")} />
                {t("tasks.fleet.error")}
              </p>
            ) : running.length === 0 ? (
              <p className="px-4 py-3 text-12 text-fg-muted">{t("tasks.fleet.nothing")}</p>
            ) : (
              <ul className="divide-y divide-border">
                {running.map((task) => (
                  <CompactRow key={task.key} task={task} onNavigate={onNavigate} />
                ))}
              </ul>
            )}
          </section>
        );
      })}
    </>
  );
}
