import { useQuery } from "@tanstack/react-query";
import { useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

import { isApiError } from "../../api/client";
import { isTimelineSource, timelineQuery, TIMELINE_SOURCES } from "../../api/queries/timeline";
import type { Timeline, TimelineEvent, TimelineMinute, TimelineProcess, TimelineSourceStatus } from "../../api/queries/timeline";
import { FilterBar } from "../../components/page/FilterBar";
import { QueryState } from "../../components/page/QueryState";
import { deployStatus } from "../../components/page/status";
import { Subsection } from "../../components/page/Subsection";
import type { ChartWindow } from "../../components/ui/Chart";
import { formatChartTime, momentNeedsDate } from "../../components/ui/chart/time";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { Drawer } from "../../components/ui/Drawer";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { LogViewer } from "../../components/ui/LogViewer";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useNeedsScrollFocus } from "../../components/ui/scrollable";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusPill } from "../../components/ui/StatusPill";
import { SystemOutput } from "../../components/ui/SystemOutput";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes, formatPercent } from "../../lib/format";
import { useNode } from "../../nodes/useNode";
import { jobActionLabel } from "../activity/data";
import { ALL, NO_FILTERS, busiestMinute, changesOf, filterEvents, logLines, unitsOf } from "./timelineData";
import type { EventFilters, LevelFilter } from "./timelineData";

export interface TimelineDrawerProps {
  /** The stretch, Unix seconds. */
  stretch: ChartWindow;
  /** Narrows it to one application, by domain. */
  app?: string | undefined;
  onClose: () => void;
}

/** The moment words of a stretch: with the date when the stretch needs it, as the chart writes it. */
function useMoment(stretch: ChartWindow): (seconds: number, withSeconds?: boolean) => string {
  const t = useT();
  const withDate = momentNeedsDate(stretch);
  return (seconds, withSeconds = false) => formatChartTime(seconds, withDate, t.locale, withSeconds);
}

/**
 * "Investigate this stretch": everything Noust knows about a stretch picked on a chart, in a
 * drawer over it. The processes that used the machine most in each minute come first (they are
 * what answers "why this peak"), then every event in order and the deployments and jobs to open.
 * What the operator may not read is said, with the permission it needs, never left out silently.
 */
export function TimelineDrawer({ stretch, app, onClose }: TimelineDrawerProps) {
  const t = useT();
  const moment = useMoment(stretch);
  const [start, end] = stretch;
  const query = useQuery(timelineQuery({ start, end, app }));
  const from = moment(start);
  const to = moment(end);
  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="lg"
      title={t("timeline.title")}
      description={app !== undefined ? t.rich("timeline.descriptionApp", { from, to, app: <Mono>{app}</Mono> }) : t("timeline.description", { from, to })}
    >
      {query.data === undefined && isApiError(query.error) && query.error.status === 404 ? (
        <OlderNoust />
      ) : (
        <QueryState query={query} label={t("timeline.queryLabel")} skeleton={<TimelineSkeleton />}>
          {(data) => <TimelineBody data={data} stretch={stretch} app={app} />}
        </QueryState>
      )}
    </Drawer>
  );
}

/**
 * A server without the timeline route answers 404: a 3.1 node behind a 3.2 central. That is
 * its version, not a failure, and it is said as the other views missing there say it.
 */
function OlderNoust() {
  const t = useT();
  const { node } = useNode();
  return <Notice title={t("timeline.olderNoustTitle")}>{node === null ? t("timeline.olderNoustHere") : t("timeline.olderNoustOnNode", { node })}</Notice>;
}

function TimelineSkeleton() {
  return (
    <div className="flex flex-col gap-6" aria-hidden="true">
      <Skeleton className="h-4 w-48" />
      <Skeleton className="h-40 w-full" />
      <Skeleton className="h-4 w-32" />
      <Skeleton className="h-64 w-full" />
    </div>
  );
}

/** A source the console knows by its name; one from a newer server as the server calls it. */
function sourceName(t: T, source: string): string {
  return isTimelineSource(source) ? t(`timeline.source.${source}`) : source;
}

function TimelineBody({ data, stretch, app }: { data: Timeline; stretch: ChartWindow; app: string | undefined }) {
  return (
    <div className="flex flex-col gap-8">
      <SourceNotices sources={data.sources} />
      {data.processes ? <ProcessesSection data={data} stretch={stretch} /> : null}
      <EventsSection data={data} stretch={stretch} app={app} />
      <ChangesSection events={data.events} stretch={stretch} />
    </div>
  );
}

/** What could not be shown, and why: a source withheld, one that failed, one that was cut. */
function SourceNotices({ sources }: { sources: readonly TimelineSourceStatus[] }) {
  const t = useT();
  const notes: ReactNode[] = [];
  for (const status of sources) {
    const source = sourceName(t, status.source);
    if (status.state === "withheld") {
      notes.push(
        <Notice key={status.source} tone="info">
          {t.rich("timeline.withheld", { source, permission: <Mono>{status.permission ?? ""}</Mono> })}
        </Notice>,
      );
    } else if (status.state === "failed") {
      notes.push(
        <Notice key={status.source} tone="warning" title={t("timeline.failed", { source })}>
          <div className="flex flex-col gap-2">
            {status.message ? <p>{status.message}</p> : null}
            {status.evidence ? <SystemOutput label={source}>{status.evidence}</SystemOutput> : null}
          </div>
        </Notice>,
      );
    } else if (status.state === "shown" && status.truncated && status.source !== "processes") {
      notes.push(
        <Notice key={status.source} tone="info">
          {t("timeline.truncated", { source, count: status.count })}
        </Notice>,
      );
    }
  }
  if (notes.length === 0) return null;
  return <div className="flex flex-col gap-2">{notes}</div>;
}

// ---------------------------------------------------------------------------------------
// Processes

function ownerOf(row: TimelineProcess): string | null {
  if (row.app && row.owner && row.owner_kind === "container") return `${row.app} · ${row.owner}`;
  return row.app ?? row.owner ?? null;
}

function ProcessesSection({ data, stretch }: { data: Timeline; stretch: ChartWindow }) {
  const t = useT();
  const moment = useMoment(stretch);
  const processes = data.processes;
  const minutes = processes?.minutes ?? [];
  const [chosen, setChosen] = useState<number | null>(null);
  const shown: TimelineMinute | null = minutes.find((minute) => minute.at === chosen) ?? busiestMinute(minutes);
  const scroller = useRef<HTMLDivElement>(null);
  const focusable = useNeedsScrollFocus(scroller);
  if (!processes) return null;

  const notes: ReactNode[] = [];
  if (processes.since === null || processes.since === undefined) {
    notes.push(<p key="never">{t("timeline.processes.neverSampled")}</p>);
  } else if (processes.since > data.start) {
    notes.push(<p key="since">{t("timeline.processes.since", { time: moment(processes.since) })}</p>);
  }
  if (processes.total_minutes > minutes.length) {
    notes.push(<p key="busiest">{t("timeline.processes.busiestOnly", { shown: minutes.length, total: processes.total_minutes })}</p>);
  }
  if (!processes.commands) {
    notes.push(<p key="commands">{t.rich("timeline.commandsHidden", { permission: <Mono>secrets.reveal</Mono> })}</p>);
  }

  const columns: Column<TimelineMinute>[] = [
    {
      id: "minute",
      header: t("timeline.processes.minute"),
      cell: (row) => moment(row.at),
      mono: true,
      width: "w-28",
    },
    {
      id: "cpu",
      header: t("timeline.processes.topCpu"),
      cell: (row) => {
        const top = row.cpu[0];
        return top ? (
          <span className="flex min-w-0 items-baseline gap-2">
            <Mono truncate>{top.name}</Mono>
            <span className="shrink-0 tabular-nums">{formatPercent(top.cpu_percent, t.locale)}</span>
          </span>
        ) : (
          <EmptyCell reason={t("timeline.processes.empty")} />
        );
      },
    },
    {
      id: "owner",
      header: t("timeline.processes.owner"),
      cell: (row) => {
        const owner = row.cpu[0] ? ownerOf(row.cpu[0]) : null;
        return owner ? <Mono tone="muted" truncate>{owner}</Mono> : <EmptyCell reason={t("timeline.processes.noOwner")} />;
      },
      hideBelow: "sm",
    },
    {
      id: "memory",
      header: t("timeline.processes.topMemory"),
      cell: (row) => {
        const top = row.memory[0];
        return top ? (
          <span className="flex min-w-0 items-baseline gap-2">
            <Mono truncate>{top.name}</Mono>
            <span className="shrink-0 tabular-nums">{formatBytes(top.memory_bytes, t.locale)}</span>
          </span>
        ) : (
          <EmptyCell reason={t("timeline.processes.empty")} />
        );
      },
    },
  ];

  return (
    <Subsection title={t("timeline.processes.title")} description={t("timeline.processes.description")}>
      <div className="flex flex-col gap-4">
        {notes.length > 0 ? (
          <Notice tone="info">
            <div className="flex flex-col gap-1">{notes}</div>
          </Notice>
        ) : null}
        {minutes.length === 0 ? (
          <p className="text-13 text-fg-muted">{t("timeline.processes.empty")}</p>
        ) : (
          <>
            <div
              ref={scroller}
              {...(focusable ? { tabIndex: 0, role: "region", "aria-label": t("timeline.processes.caption") } : {})}
              className="max-h-72 overflow-y-auto scroll-thin -outline-offset-2"
            >
              <DataTable
                columns={columns}
                rows={minutes}
                getRowId={(row) => String(row.at)}
                caption={t("timeline.processes.caption")}
                density="compact"
                layout="fixed"
                onRowActivate={(row) => {
                  setChosen(row.at);
                }}
              />
            </div>
            {shown ? <MinuteDetail minute={shown} stretch={stretch} commands={processes.commands} /> : null}
          </>
        )}
      </div>
    </Subsection>
  );
}

function MinuteDetail({ minute, stretch, commands }: { minute: TimelineMinute; stretch: ChartWindow; commands: boolean }) {
  const t = useT();
  const moment = useMoment(stretch);
  const time = moment(minute.at);
  const columns: Column<TimelineProcess>[] = [
    { id: "process", header: t("timeline.processes.process"), cell: (row) => <Mono truncate>{row.name}</Mono>, width: "w-32" },
    {
      id: "owner",
      header: t("timeline.processes.owner"),
      cell: (row) => {
        const owner = ownerOf(row);
        return owner ? <Mono tone="muted" truncate>{owner}</Mono> : <EmptyCell reason={t("timeline.processes.noOwner")} />;
      },
    },
    {
      id: "pid",
      header: t("timeline.processes.pid"),
      cell: (row) => row.pid,
      mono: true,
      align: "end",
      width: "w-20",
      hideBelow: "md",
    },
    { id: "user", header: t("timeline.processes.user"), cell: (row) => <Mono truncate>{row.user}</Mono>, width: "w-24", hideBelow: "md" },
    {
      id: "cpu",
      header: t("timeline.processes.cpu"),
      cell: (row) => formatPercent(row.cpu_percent, t.locale),
      mono: true,
      align: "end",
      width: "w-20",
    },
    {
      id: "memory",
      header: t("timeline.processes.memory"),
      cell: (row) => formatBytes(row.memory_bytes, t.locale),
      mono: true,
      align: "end",
      width: "w-24",
    },
    {
      id: "command",
      header: t("timeline.processes.command"),
      cell: (row) =>
        row.command ? (
          <Mono tone="muted" truncate title={row.command}>
            {row.command}
          </Mono>
        ) : (
          <EmptyCell reason={commands ? t("timeline.processes.empty") : t("timeline.processes.hiddenCommand")} />
        ),
      hideBelow: "lg",
    },
  ];
  return (
    <Subsection title={t("timeline.processes.selected", { time })} level={4}>
      <div className="flex flex-col gap-4" aria-live="polite">
        <div className="flex flex-col gap-2">
          <p className="text-12 font-medium text-fg-muted">{t("timeline.processes.byCpu")}</p>
          <DataTable
            columns={columns}
            rows={minute.cpu}
            getRowId={(row) => `cpu-${String(row.position)}`}
            caption={t("timeline.processes.cpuCaption", { time })}
            density="compact"
            layout="fixed"
          />
        </div>
        <div className="flex flex-col gap-2">
          <p className="text-12 font-medium text-fg-muted">{t("timeline.processes.byMemory")}</p>
          <DataTable
            columns={columns}
            rows={minute.memory}
            getRowId={(row) => `memory-${String(row.position)}`}
            caption={t("timeline.processes.memoryCaption", { time })}
            density="compact"
            layout="fixed"
          />
        </div>
      </div>
    </Subsection>
  );
}

// ---------------------------------------------------------------------------------------
// Events

function EventsSection({ data, stretch, app }: { data: Timeline; stretch: ChartWindow; app: string | undefined }) {
  const t = useT();
  const moment = useMoment(stretch);
  const [filters, setFilters] = useState<EventFilters>(NO_FILTERS);
  const shownSources = data.sources.filter((status) => status.state === "shown" && status.source !== "processes").map((status) => status.source);
  const units = useMemo(() => unitsOf(data.events), [data.events]);
  const events = useMemo(() => filterEvents(data.events, filters), [data.events, filters]);
  const lines = useMemo(() => logLines(events, t.locale), [events, t.locale]);
  const filtered = filters.source !== ALL || filters.level !== ALL || filters.unit !== ALL;

  const sourceOptions = [
    { value: ALL, label: t("timeline.events.everySource") },
    ...TIMELINE_SOURCES.filter((source) => shownSources.includes(source)).map((source) => ({ value: source, label: sourceName(t, source) })),
  ];
  const levelOptions: { value: LevelFilter; label: string }[] = [
    { value: ALL, label: t("timeline.events.everyLevel") },
    { value: "error", label: t("timeline.events.errorsOnly") },
    { value: "warning", label: t("timeline.events.warningsUp") },
    { value: "notice", label: t("timeline.events.noticesUp") },
  ];
  const unitOptions = [{ value: ALL, label: t("timeline.events.everyUnit") }, ...units.map((unit) => ({ value: unit, label: unit }))];

  return (
    <Subsection title={t("timeline.events.title")} description={app !== undefined ? t("timeline.events.descriptionApp") : t("timeline.events.description")}>
      <div className="flex flex-col gap-3">
        <FilterBar
          label={t("timeline.events.filterLabel")}
          filters={
            <>
              <Select
                aria-label={t("timeline.events.source")}
                options={sourceOptions}
                value={filters.source}
                onValueChange={(source) => {
                  setFilters((current) => ({ ...current, source: source as EventFilters["source"] }));
                }}
              />
              <Select
                aria-label={t("timeline.events.level")}
                options={levelOptions}
                value={filters.level}
                onValueChange={(level) => {
                  setFilters((current) => ({ ...current, level }));
                }}
              />
              {units.length > 0 ? (
                <Select
                  aria-label={t("timeline.events.unit")}
                  options={unitOptions}
                  value={filters.unit}
                  mono
                  onValueChange={(unit) => {
                    setFilters((current) => ({ ...current, unit }));
                  }}
                />
              ) : null}
            </>
          }
          count={
            filtered
              ? t("timeline.events.countFiltered", { shown: events.length, total: data.events.length })
              : t("timeline.events.count", { count: data.events.length })
          }
        />
        <LogViewer
          lines={lines}
          height={360}
          searchable
          label={t("timeline.events.label", { from: moment(data.start), to: moment(data.end) })}
          filename={`noust-timeline-${String(data.start)}-${String(data.end)}.log`}
          emptyMessage={filtered ? t("timeline.events.noMatch") : t("timeline.events.empty")}
        />
      </div>
    </Subsection>
  );
}

// ---------------------------------------------------------------------------------------
// Deployments and jobs

function ChangesSection({ events, stretch }: { events: readonly TimelineEvent[]; stretch: ChartWindow }) {
  const t = useT();
  const moment = useMoment(stretch);
  const rows = changesOf(events);
  if (rows.length === 0) return null;

  const columns: Column<TimelineEvent>[] = [
    {
      id: "what",
      header: t("timeline.changes.what"),
      cell: (row) => {
        if (row.kind === "deployment" && row.app && row.ref) {
          return (
            <TextLink to="/apps/$domain/deployments/$id" params={{ domain: row.app, id: row.ref }} size="ui">
              {t("timeline.changes.deployment", { id: row.ref, app: row.app })}
            </TextLink>
          );
        }
        const type = typeof row.details?.["type"] === "string" ? row.details["type"] : "";
        return (
          <span className="flex min-w-0 items-baseline gap-2">
            <TextLink to="/activity" size="ui">
              {jobActionLabel(t, type)}
            </TextLink>
            <Mono tone="muted" truncate title={row.text}>
              {row.text}
            </Mono>
          </span>
        );
      },
    },
    {
      id: "state",
      header: t("timeline.changes.state"),
      cell: (row) => {
        const view = deployStatus(row.status, t.locale);
        return <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" />;
      },
      width: "w-32",
    },
    {
      id: "started",
      header: t("timeline.changes.started"),
      cell: (row) => moment(row.at, true),
      mono: true,
      width: "w-28",
    },
    {
      id: "who",
      header: t("timeline.changes.who"),
      cell: (row) => (row.actor ? <Mono truncate>{row.actor}</Mono> : <EmptyCell reason={t("timeline.changes.notRecorded")} />),
      hideBelow: "sm",
      width: "w-32",
    },
  ];

  return (
    <Subsection title={t("timeline.changes.title")}>
      <DataTable columns={columns} rows={rows} getRowId={(row) => `${row.kind}-${row.ref ?? String(row.at)}`} caption={t("timeline.changes.caption")} density="compact" layout="fixed" />
    </Subsection>
  );
}
