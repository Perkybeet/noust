/**
 * The journal of any unit on the machine, the kernel's messages, or everything: filtered by
 * level, time and text, read verbatim in a viewer the height of the screen, and followed live
 * on request. Failed units come first in the unit list.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { CommandHint } from "../../../components/page/CommandHint";
import { FilterBar } from "../../../components/page/FilterBar";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { LogViewer } from "../../../components/ui/LogViewer";
import { Select } from "../../../components/ui/Select";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { ServerErrorBlock } from "../errors";
import { SERVER_CAPABILITY, journalQuery, journalUnitsQuery } from "../queries";
import { KERNEL, journalFilters, journalLines, logsCommand } from "./data";
import type { LogPriority, LogRange, LogsSearch } from "./data";

/** Lines read at first, and the most the API returns in one read. */
const LINES = 300;
const MORE_LINES = 1000;
/** How often a followed journal is read again. */
const FOLLOW_MS = 5_000;
/** Select's value for "no filter": an option needs a value. */
const ALL = "__all";

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

function Logs({ search, onSearchChange }: LogsTabProps) {
  const t = useT();
  const [lines, setLines] = useState(LINES);
  const [follow, setFollow] = useState(false);
  const units = useQuery(journalUnitsQuery());
  const filters = journalFilters(search, lines);
  const journal = useQuery({ ...journalQuery(filters), refetchInterval: follow ? FOLLOW_MS : false });
  const shown = useMemo(() => (journal.data ? journalLines(journal.data) : []), [journal.data]);

  const set = (patch: Partial<Record<keyof LogsSearch, string | undefined>>): void => {
    const next: Record<string, string | undefined> = { ...search, ...patch };
    const clean = Object.fromEntries(Object.entries(next).filter(([, value]) => value !== undefined && value !== "")) as LogsSearch;
    setLines(LINES);
    onSearchChange(clean);
  };

  const unitOptions = [
    { value: ALL, label: t("server.logs.allUnits") },
    { value: KERNEL, label: t("server.logs.kernel") },
    ...(units.data ?? []).map((unit) => ({ value: unit.name, label: unit.failed ? t("server.logs.failedUnit", { unit: unit.name }) : unit.name })),
    // A unit named in the address that the list does not have (yet) still shows as chosen.
    ...(search.unit !== undefined && search.unit !== KERNEL && !(units.data ?? []).some((unit) => unit.name === search.unit) ? [{ value: search.unit, label: search.unit }] : []),
  ];

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
            <SegmentedControl<LogRange>
              label={t("server.logs.rangeLabel")}
              value={search.range ?? "1h"}
              onValueChange={(range) => set({ range: range === "1h" ? undefined : range })}
              options={rangeOptions(t)}
            />
          </>
        }
        actions={<Switch label={t("server.logs.follow")} checked={follow} onCheckedChange={setFollow} />}
      />
      {journal.isError ? (
        <ServerErrorBlock compact error={journal.error} title={t("server.logs.loadFailed")} onRetry={() => void journal.refetch()} retrying={journal.isRefetching} />
      ) : null}
      <div className="flex h-editor min-h-0 min-w-0 flex-col">
        <LogViewer
          lines={shown}
          height="fill"
          follow
          // The search above asks the journal itself, past the lines on screen: one search box.
          searchable={false}
          label={t("server.logs.viewerLabel")}
          filename={`${search.unit ?? "journal"}.log`}
          emptyMessage={journal.isPending ? t("server.logs.loading") : t("server.logs.empty")}
        />
      </div>
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
        <CommandHint command={logsCommand(search)} label={t("server.fromTerminal")} />
        {journal.data?.truncated === true && lines < MORE_LINES ? (
          <Button size="sm" variant="ghost" onClick={() => setLines(MORE_LINES)}>
            {t("server.logs.more", { count: MORE_LINES })}
          </Button>
        ) : journal.data?.truncated === true ? (
          <span className="text-12 text-fg-muted">{t("server.logs.truncated", { count: lines })}</span>
        ) : null}
      </div>
    </div>
  );
}

/** The Logs tab. */
export function LogsTab(props: LogsTabProps) {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      <Logs {...props} />
    </NodeCapabilityGate>
  );
}
