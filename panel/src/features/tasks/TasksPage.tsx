import { keepPreviousData, useQuery } from "@tanstack/react-query";
import { Link, useNavigate } from "@tanstack/react-router";
import { useEffect, useMemo, useState } from "react";

import { jobsQuery } from "../../api/queries/jobs";
import { ListPage } from "../../components/page/ListPage";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { formatDuration } from "../../lib/format";
import { useHasFleet, useServerList } from "../../nodes/servers";
import { useNode } from "../../nodes/useNode";
import { QueryState } from "../../components/page/QueryState";
import type { QueryLike } from "../../components/page/QueryState";
import { STATE_RANK } from "../../components/page/status";
import { useMe } from "./feeds";
import { isActive, placeOf } from "./model";
import type { Task } from "./model";
import { taskStore, useTaskState, withJobs } from "./store";
import { whoOf } from "./who";
import { titleOf, viewOf } from "./words";

/** The jobs the first page of the history asks for, and the most the server gives (`GET /api/jobs` takes up to 100). */
const FIRST = 50;
const MOST = 100;

/** When a task began, or was asked for: what the list is ordered by. */
function sinceOf(task: Task): number {
  return task.startedAt ?? task.createdAt ?? task.touched;
}

function took(task: Task): number | null {
  const began = task.startedAt ?? task.createdAt;
  return !isActive(task) && task.completedAt !== null && began !== null ? Math.max(0, (task.completedAt - began) / 1000) : null;
}

/**
 * Activity > Tasks (item 114): what the server has done and is doing, latest first. The history is read from the
 * server and taken into the task centre's store with what the events have said since, so the list and the panel under the
 * top bar are one set of tasks. A team's role sees what its own scope has; the server decides it.
 */
export function TasksPage() {
  const t = useT();
  const me = useMe();
  const { node } = useNode();
  const navigate = useNavigate();
  const hasFleet = useHasFleet();
  const { hostname } = useServerList();
  const [limit, setLimit] = useState(FIRST);
  const history = useQuery({ ...jobsQuery({ limit }), retry: false, placeholderData: keepPreviousData });
  const state = useTaskState();

  useEffect(() => {
    if (history.data === undefined) return;
    const { jobs } = history.data;
    const at = history.dataUpdatedAt;
    taskStore.update((current) => withJobs(current, jobs, node, at, at - 1_500));
  }, [history.data, history.dataUpdatedAt, node]);

  const rows = useMemo(() => [...state.tasks.values()].filter((task) => task.server === node).sort((a, b) => sinceOf(b) - sinceOf(a)), [state.tasks, node]);
  const more = history.data !== undefined && history.data.jobs.length >= limit && limit < MOST;
  // What the table draws is the store's, with the history behind it: nothing to say until one of them has answered.
  const read: QueryLike<Task[]> = {
    data: history.data === undefined && rows.length === 0 ? undefined : rows,
    error: history.error,
    isPending: history.isPending && rows.length === 0,
    isError: history.isError,
    isRefetching: history.isRefetching,
    refetch: history.refetch,
  };

  const columns: Column<Task>[] = [
    {
      id: "task",
      header: t("tasks.list.columns.task"),
      card: "title",
      cell: (task) => <span className="block max-w-96 truncate text-fg">{titleOf(task, t.locale)}</span>,
      sortValue: (task) => titleOf(task, t.locale),
    },
    {
      id: "state",
      header: t("tasks.list.columns.state"),
      width: "w-36",
      card: "status",
      cell: (task) => {
        const view = viewOf(task.state, t.locale);
        return <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" />;
      },
      sortValue: (task) => STATE_RANK[viewOf(task.state, t.locale).state],
    },
    {
      id: "who",
      header: t("tasks.list.columns.who"),
      width: "w-44",
      hideBelow: "sm",
      cell: (task) => <span className="block max-w-44 truncate text-fg-muted">{whoOf(task, me, t)}</span>,
      sortValue: (task) => whoOf(task, me, t),
    },
    ...(hasFleet
      ? [
          {
            id: "where",
            header: t("tasks.list.columns.where"),
            width: "w-36",
            hideBelow: "md" as const,
            cell: () => <Mono tone="muted">{node ?? hostname ?? ""}</Mono>,
          },
        ]
      : []),
    {
      id: "when",
      header: t("tasks.list.columns.when"),
      width: "w-48",
      cell: (task) => {
        const duration = took(task);
        return duration === null ? (
          <RelativeTime value={sinceOf(task)} />
        ) : (
          <span>{t.rich("tasks.row.tookFor", { when: <RelativeTime key="when" value={task.completedAt} />, duration: formatDuration(duration, t.locale) })}</span>
        );
      },
      sortValue: (task) => sinceOf(task),
    },
  ];

  const open = (task: Task): void => {
    const place = placeOf(task);
    void navigate({ to: place.to as never, ...(place.params !== undefined ? { params: place.params as never } : {}), ...(place.search !== undefined ? { search: place.search as never } : {}) });
  };

  return (
    <ListPage header={{ title: t("tasks.list.title"), description: t("tasks.list.description") }} footer={<p className="text-12 text-fg-muted">{t("tasks.list.centre")}</p>}>
      <QueryState
        query={read}
        sentences={{ loading: t("tasks.list.loading"), failed: t("tasks.list.readFailed"), refreshFailed: t("tasks.list.refreshFailed") }}
        skeleton={<Skeleton className="h-64 w-full rounded-card" />}
        isEmpty={(all) => all.length === 0}
        empty={<EmptyState variant="firstUse" icon={<OBJECT_ICONS.work />} title={t("tasks.list.emptyTitle")} description={t("tasks.list.emptyDescription")} />}
      >
        {(all) => (
          <>
            <DataTable
              columns={columns}
              rows={all}
              getRowId={(task) => task.key}
              caption={t("tasks.list.caption")}
              defaultSort={{ column: "when", direction: "descending" }}
              onRowActivate={open}
              rowActions={(task) => {
                const place = placeOf(task);
                return (
                  <Link
                    to={place.to as never}
                    {...(place.params !== undefined ? { params: place.params as never } : {})}
                    {...(place.search !== undefined ? { search: place.search as never } : {})}
                    aria-label={t("tasks.list.open", { title: titleOf(task, t.locale) })}
                    className="rounded-control px-1.5 py-0.5 text-13 font-medium text-accent-fg hover:bg-accent-soft"
                  >
                    {t("tasks.row.open")}
                  </Link>
                );
              }}
            />
            {more ? (
              <div className="flex justify-center">
                <Button
                  loading={history.isFetching}
                  onClick={() => {
                    setLimit(MOST);
                  }}
                >
                  {t("tasks.list.more")}
                </Button>
              </div>
            ) : null}
          </>
        )}
      </QueryState>
    </ListPage>
  );
}
