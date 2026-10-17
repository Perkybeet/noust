/**
 * The journal of any unit on the machine, the kernel's messages, or everything: searched with a
 * query (words, fields, AND, OR, -, quotes, groups: `@unit:nginx error -timeout`), by level and by
 * time, read verbatim in a viewer the height of the screen, followed live on request, opened at any
 * entry to see what surrounded it, and joined to "what happened in this stretch". The query is
 * read and judged by the server; the pickers are shortcuts for its fields, and the page says the
 * query as the server understood it. Failed units come first in the unit list.
 */

import { useQuery } from "@tanstack/react-query";
import { CircleHelp } from "lucide-react";
import { useMemo, useState } from "react";

import { CommandHint } from "../../../components/page/CommandHint";
import { FilterBar } from "../../../components/page/FilterBar";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { IconButton } from "../../../components/ui/IconButton";
import { LogViewer } from "../../../components/ui/LogViewer";
import type { LogLine } from "../../../components/ui/LogViewer";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Popover } from "../../../components/ui/Popover";
import { Select } from "../../../components/ui/Select";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatDateTime } from "../../../lib/format";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { TimelineDrawer } from "../../timeline/TimelineDrawer";
import { ServerErrorBlock } from "../errors";
import { journalSearchQuery, journalUnitsQuery } from "../queries";
import { ContextDrawer } from "./ContextDrawer";
import type { ContextEntry } from "./ContextDrawer";
import { KERNEL, SCAN, SCAN_FURTHER, journalLines, logsCommand, searchFilters, stretchOf } from "./data";
import type { LogPriority, LogRange, LogsSearch } from "./data";

/** Lines shown at first, and the most the API returns in one search. */
const LINES = 300;
const MORE_LINES = 1000;
/** How often a followed journal is read again. */
const FOLLOW_MS = 5_000;
/** Select's value for "no filter": an option needs a value. */
const ALL = "__all";

/** The capability this tab needs of the server on screen: a node older than 3.4 has no query. */
const SEARCH_CAPABILITY = "GET /api/server/logs/search";

export interface LogsTabProps {
  search: LogsSearch;
  onSearchChange: (search: LogsSearch) => void;
}

function priorityOptions(t: T): { value: LogPriority | typeof ALL; label: string }[] {
  return [
    { value: ALL, label: t("server.logs.priority.all") },
    { value: "err", label: t("server.logs.priority.err") },
    { value: "warning", label: t("server.logs.priority.warning") },
    { value: "notice", label: t("server.logs.priority.notice") },
    { value: "info", label: t("server.logs.priority.info") },
  ];
}

function rangeOptions(t: T): { value: LogRange; label: string }[] {
  return [
    { value: "1h", label: t("server.logs.range.hour") },
    { value: "24h", label: t("server.logs.range.day") },
    { value: "7d", label: t("server.logs.range.week") },
    { value: "boot", label: t("server.logs.range.boot") },
    { value: "previous", label: t("server.logs.range.previous") },
  ];
}

/** The query language in a table of examples: what is typed is the syntax, so it is not translated. */
const SYNTAX: readonly (readonly [string, "words" | "phrase" | "unit" | "app" | "level" | "not" | "or" | "group"])[] = [
  ["error timeout", "words"],
  ['"connection refused"', "phrase"],
  ["@unit:nginx", "unit"],
  ["@app:shop.example.com", "app"],
  ["@level:err", "level"],
  ["-debug", "not"],
  ["error OR fatal", "or"],
  ["(a OR b) c", "group"],
];

function QuerySyntax() {
  const t = useT();
  return (
    <Popover
      align="end"
      title={t("server.logs.syntax.title")}
      description={t("server.logs.syntax.intro")}
      className="w-96"
      trigger={<IconButton label={t("server.logs.syntax.label")} icon={<CircleHelp />} />}
    >
      <dl className="flex flex-col gap-2 text-13">
        {SYNTAX.map(([example, meaning]) => (
          <div key={example} className="flex min-w-0 flex-col gap-0.5">
            <dt>
              <Mono>{example}</Mono>
            </dt>
            <dd className="text-fg-muted">{t(`server.logs.syntax.${meaning}`)}</dd>
          </div>
        ))}
      </dl>
    </Popover>
  );
}

function Logs({ search, onSearchChange }: LogsTabProps) {
  const t = useT();
  const [lines, setLines] = useState(LINES);
  const [scan, setScan] = useState(SCAN);
  const [follow, setFollow] = useState(false);
  const [around, setAround] = useState<ContextEntry | null>(null);
  const [investigating, setInvestigating] = useState<[number, number] | null>(null);
  const units = useQuery(journalUnitsQuery());
  const filters = searchFilters(search, lines, scan);
  const journal = useQuery({ ...journalSearchQuery(filters), refetchInterval: follow ? FOLLOW_MS : false });
  const entries = journal.data?.entries;
  const shown = useMemo(() => (journal.data ? journalLines(journal.data) : []), [journal.data]);
  // Whether the range is a stretch of time at all; the moment it ends is read when it is asked for.
  const hasStretch = stretchOf(search, 0) !== null;

  const set = (patch: Partial<Record<keyof LogsSearch, string | number | undefined>>): void => {
    const next: Record<string, string | number | undefined> = { ...search, ...patch };
    const clean = Object.fromEntries(Object.entries(next).filter(([, value]) => value !== undefined && value !== "")) as LogsSearch;
    setLines(LINES);
    setScan(SCAN);
    onSearchChange(clean);
  };

  const open = (line: LogLine): void => {
    const entry = entries?.[line.id];
    if (entry !== undefined) setAround({ cursor: entry.cursor, unit: entry.unit, timestamp: entry.timestamp });
  };

  const unitOptions = [
    { value: ALL, label: t("server.logs.allUnits") },
    { value: KERNEL, label: t("server.logs.kernel") },
    ...(units.data ?? []).map((unit) => ({ value: unit.name, label: unit.failed ? t("server.logs.failedUnit", { unit: unit.name }) : unit.name })),
    // A unit named in the address that the list does not have (yet) still shows as chosen.
    ...(search.unit !== undefined && search.unit !== KERNEL && !(units.data ?? []).some((unit) => unit.name === search.unit) ? [{ value: search.unit, label: search.unit }] : []),
  ];
  const customStretch = search.from !== undefined && search.to !== undefined;
  const result = journal.data;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <FilterBar
        label={t("server.logs.filterLabel")}
        search={{ label: t("server.logs.searchLabel"), placeholder: t("server.logs.searchPlaceholder"), value: search.q ?? "", onChange: (q) => set({ q }) }}
        filters={
          <>
            <Select
              aria-label={t("server.logs.unitLabel")}
              value={search.unit ?? ALL}
              onValueChange={(unit) => set({ unit: unit === ALL ? undefined : unit })}
              options={unitOptions}
              mono
              className="w-56"
            />
            <Select
              aria-label={t("server.logs.priorityLabel")}
              value={search.priority ?? ALL}
              onValueChange={(priority) => set({ priority: priority === ALL ? undefined : priority })}
              options={priorityOptions(t)}
            />
            {customStretch ? null : (
              <SegmentedControl<LogRange>
                label={t("server.logs.rangeLabel")}
                value={search.range ?? "1h"}
                onValueChange={(range) => set({ range: range === "1h" ? undefined : range })}
                options={rangeOptions(t)}
              />
            )}
          </>
        }
        actions={
          <>
            <QuerySyntax />
            <Switch label={t("server.logs.follow")} checked={follow} onCheckedChange={setFollow} />
          </>
        }
      />
      {customStretch && search.from !== undefined && search.to !== undefined ? (
        <Notice
          tone="info"
          action={
            <Button size="sm" onClick={() => set({ from: undefined, to: undefined })}>
              {t("server.logs.stretchClear")}
            </Button>
          }
        >
          {t("server.logs.stretch", { from: formatDateTime(new Date(search.from * 1000), t.locale), to: formatDateTime(new Date(search.to * 1000), t.locale) })}
        </Notice>
      ) : null}
      {journal.isError ? (
        <ServerErrorBlock compact error={journal.error} title={t("server.logs.loadFailed")} onRetry={() => void journal.refetch()} retrying={journal.isRefetching} />
      ) : null}
      {result?.scan_limit_reached === true ? (
        <Notice
          tone="info"
          title={t("server.logs.scanTitle")}
          action={
            scan < SCAN_FURTHER ? (
              <Button size="sm" onClick={() => setScan(SCAN_FURTHER)}>
                {t("server.logs.scanFurther", { count: SCAN_FURTHER })}
              </Button>
            ) : undefined
          }
        >
          {t("server.logs.scanBody", { count: result.scanned })}
        </Notice>
      ) : null}
      <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-12 text-fg-muted">
        <p role="status">
          {result === undefined
            ? t("server.logs.loading")
            : result.oldest
              ? t("server.logs.result", { matched: result.matched, scanned: result.scanned, since: formatDateTime(new Date(result.oldest), t.locale) })
              : t("server.logs.resultNone", { matched: result.matched, scanned: result.scanned })}
        </p>
        {result !== undefined && result.normalised !== "" ? (
          <p>
            {t("server.logs.understood")} <Mono>{result.normalised}</Mono>
          </p>
        ) : null}
        {hasStretch ? (
          <Button size="sm" variant="ghost" onClick={() => setInvestigating(stretchOf(search, Math.floor(Date.now() / 1000)))}>
            {t("server.logs.whatHappened")}
          </Button>
        ) : null}
      </div>
      <div className="flex h-editor min-h-0 min-w-0 flex-col">
        <LogViewer
          lines={shown}
          height="fill"
          follow
          // The search above asks the journal itself, past the lines on screen: one search box.
          searchable={false}
          loading={journal.isPending}
          onOpenLine={open}
          openLineLabel={t("server.logs.openLine")}
          label={t("server.logs.viewerLabel")}
          filename={`${search.unit ?? "journal"}.log`}
          emptyMessage={journal.isPending ? t("server.logs.loading") : t("server.logs.empty")}
        />
      </div>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
        <CommandHint command={logsCommand(search)} label={t("server.fromTerminal")} />
        {result?.truncated === true && lines < MORE_LINES ? (
          <Button size="sm" variant="ghost" onClick={() => setLines(MORE_LINES)}>
            {t("server.logs.more", { count: MORE_LINES })}
          </Button>
        ) : result?.truncated === true ? (
          <span className="text-12 text-fg-muted">{t("server.logs.truncated", { count: lines })}</span>
        ) : null}
      </div>
      {around !== null ? <ContextDrawer entry={around} onClose={() => setAround(null)} /> : null}
      {investigating !== null ? <TimelineDrawer stretch={investigating} onClose={() => setInvestigating(null)} /> : null}
    </div>
  );
}

/** The Logs tab. */
export function LogsTab(props: LogsTabProps) {
  return (
    <NodeCapabilityGate capability={SEARCH_CAPABILITY}>
      <Logs {...props} />
    </NodeCapabilityGate>
  );
}
