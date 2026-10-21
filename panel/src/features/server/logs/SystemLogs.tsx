/**
 * The system's logs, one source of Logs: the journal of any unit on the machine, the kernel's
 * messages, or everything, searched with the language of `features/logs/query.ts` (words, fields,
 * AND, OR, -, quotes, groups: `@unit:nginx error -timeout`), by level and by time. It is read
 * verbatim in a viewer the height of the screen, followed live when the range is "Live", opened at
 * any entry to see what surrounded it, and joined to "what happened in this stretch". The query is
 * judged by the server, which writes it back as it understood it. Failed services come first in
 * the service list.
 */

import { useQuery } from "@tanstack/react-query";
import { History } from "lucide-react";
import { useMemo, useState } from "react";

import { QueryState } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { LogViewer } from "../../../components/ui/LogViewer";
import type { LogLine } from "../../../components/ui/LogViewer";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import { formatDateTime } from "../../../lib/format";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { KERNEL, LIVE_MS, SCAN, SCAN_FURTHER, SYSTEM_LINES, SYSTEM_MORE_LINES, isLive, journalFilters, rangeOf, stretchOf } from "../../logs/data";
import type { LogsSearch } from "../../logs/data";
import { LogToolbar } from "../../logs/LogToolbar";
import { patched, rangeChoices, sourceChoices, switchedTo } from "../../logs/sources";
import type { Patch } from "../../logs/sources";
import { TimelineDrawer } from "../../timeline/TimelineDrawer";
import { journalSearchQuery, journalUnitsQuery } from "../queries";
import { ContextDrawer } from "./ContextDrawer";
import { journalLines } from "./data";

/** Select's value for "no filter": an option needs a value. */
const ALL = "__all";

/** The capability this source needs of the server on screen: a node older than 3.4 has no query. */
const SEARCH_CAPABILITY = "GET /api/server/logs/search";

export interface SystemLogsProps {
  search: LogsSearch;
  /** `push` adds a history entry: opening a line is a place Back should leave. */
  onSearchChange: (search: LogsSearch, options?: { push?: boolean }) => void;
}

function Logs({ search, onSearchChange }: SystemLogsProps) {
  const t = useT();
  const [lines, setLines] = useState(SYSTEM_LINES);
  const [scan, setScan] = useState(SCAN);
  const [investigating, setInvestigating] = useState<[number, number] | null>(null);
  const units = useQuery(journalUnitsQuery());
  const filters = journalFilters(search, lines, scan);
  const journal = useQuery({ ...journalSearchQuery(filters), refetchInterval: isLive(search) ? LIVE_MS : false });
  const entries = journal.data?.entries;
  const shown = useMemo(() => (journal.data ? journalLines(journal.data) : []), [journal.data]);
  // Whether the range is a stretch of time at all; the moment it ends is read when it is asked for.
  const hasStretch = stretchOf(search, 0) !== null;
  const customStretch = search.from !== undefined && search.to !== undefined;

  const set = (patch: Patch): void => {
    setLines(SYSTEM_LINES);
    setScan(SCAN);
    onSearchChange(patched(search, patch));
  };

  const open = (line: LogLine): void => {
    const entry = entries?.[line.id];
    if (entry !== undefined) onSearchChange(patched(search, { line: entry.cursor }), { push: true });
  };

  const unitOptions = [
    { value: ALL, label: t("logs.system.allUnits") },
    { value: KERNEL, label: t("logs.system.kernel") },
    ...(units.data ?? []).map((unit) => ({ value: unit.name, label: unit.failed ? t("logs.system.failedUnit", { unit: unit.name }) : unit.name })),
    // A unit named in the address that the list does not have (yet) still shows as chosen.
    ...(search.unit !== undefined && search.unit !== KERNEL && !(units.data ?? []).some((unit) => unit.name === search.unit) ? [{ value: search.unit, label: search.unit }] : []),
  ];
  const opened = entries?.find((entry) => entry.cursor === search.line);

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <LogToolbar
        label={t("logs.filterLabel")}
        query={{
          value: search.q ?? "",
          onChange: (q) => {
            set({ q });
          },
          label: t("logs.searchLabel"),
          placeholder: t("logs.system.searchPlaceholder"),
        }}
        sources={{
          label: t("logs.sourceLabel"),
          value: "system",
          options: sourceChoices(t),
          onChange: (source) => {
            onSearchChange(switchedTo(search, source));
          },
        }}
        picker={
          <Select
            aria-label={t("logs.system.unitLabel")}
            value={search.unit ?? ALL}
            onValueChange={(unit) => {
              set({ unit: unit === ALL ? undefined : unit });
            }}
            options={unitOptions}
            mono
            className="w-56"
          />
        }
        range={{
          label: t("logs.windowLabel"),
          value: rangeOf(search),
          options: rangeChoices(t, "system"),
          hidden: customStretch,
          onChange: (since) => {
            set({ since, from: undefined, to: undefined });
          },
        }}
        understood={journal.data?.normalised}
      />
      {customStretch && search.from !== undefined && search.to !== undefined ? (
        <Notice
          tone="info"
          action={
            <Button
              size="sm"
              onClick={() => {
                set({ from: undefined, to: undefined });
              }}
            >
              {t("logs.system.stretchClear")}
            </Button>
          }
        >
          {t("logs.system.stretch", { from: formatDateTime(new Date(search.from * 1000), t.locale), to: formatDateTime(new Date(search.to * 1000), t.locale) })}
        </Notice>
      ) : null}
      <QueryState
        query={journal}
        label={t("logs.system.noun")}
        skeleton={
          <div className="flex h-editor min-h-0 min-w-0 flex-col">
            <LogViewer lines={[]} height="fill" searchable={false} loading label={t("logs.system.viewerLabel")} emptyMessage={t("logs.system.loading")} />
          </div>
        }
      >
        {(result) => (
          <div className="flex min-w-0 flex-col gap-4">
            {result.scan_limit_reached ? (
              <Notice
                tone="info"
                title={t("logs.system.scanTitle")}
                action={
                  scan < SCAN_FURTHER ? (
                    <Button
                      size="sm"
                      onClick={() => {
                        setScan(SCAN_FURTHER);
                      }}
                    >
                      {t("logs.system.scanFurther", { count: SCAN_FURTHER })}
                    </Button>
                  ) : undefined
                }
              >
                {t("logs.system.scanBody", { count: result.scanned })}
              </Notice>
            ) : null}
            <div className="flex min-w-0 flex-wrap items-baseline justify-between gap-x-4 gap-y-1 text-12 text-fg-muted">
              <p role="status">
                {result.oldest
                  ? t("logs.system.result", { matched: result.matched, scanned: result.scanned, since: formatDateTime(new Date(result.oldest), t.locale) })
                  : t("logs.system.resultNone", { matched: result.matched, scanned: result.scanned })}
              </p>
              {hasStretch ? (
                <Button
                  size="sm"
                  variant="ghost"
                  icon={<History aria-hidden="true" />}
                  onClick={() => {
                    setInvestigating(stretchOf(search, Math.floor(Date.now() / 1000)));
                  }}
                >
                  {t("logs.system.whatHappened")}
                </Button>
              ) : null}
            </div>
            {/* Keyed by the range: choosing "Live" starts the viewer following the end again. */}
            <div key={`${rangeOf(search)}-${search.unit ?? ""}`} className="flex h-editor min-h-0 min-w-0 flex-col">
              <LogViewer
                lines={shown}
                height="fill"
                follow
                // The search above asks the journal itself, past the lines on screen: one search box.
                searchable={false}
                onOpenLine={open}
                openLineLabel={t("logs.system.openLine")}
                label={t("logs.system.viewerLabel")}
                filename={`${search.unit ?? "journal"}.log`}
                emptyMessage={t("logs.system.empty")}
              />
            </div>
            {result.truncated && lines < SYSTEM_MORE_LINES ? (
              <div>
                <Button
                  size="sm"
                  variant="ghost"
                  onClick={() => {
                    setLines(SYSTEM_MORE_LINES);
                  }}
                >
                  {t("logs.system.more", { count: SYSTEM_MORE_LINES })}
                </Button>
              </div>
            ) : result.truncated ? (
              <p className="text-12 text-fg-muted">{t("logs.system.truncated", { count: lines })}</p>
            ) : null}
          </div>
        )}
      </QueryState>
      {search.line !== undefined ? (
        <ContextDrawer
          entry={{ cursor: search.line, unit: opened?.unit ?? "", timestamp: opened?.timestamp ?? "" }}
          onClose={() => {
            set({ line: undefined });
          }}
        />
      ) : null}
      {investigating !== null ? (
        <TimelineDrawer
          stretch={investigating}
          onClose={() => {
            setInvestigating(null);
          }}
        />
      ) : null}
    </div>
  );
}

/** The system's logs as a source of Logs. */
export function SystemLogs(props: SystemLogsProps) {
  return (
    <NodeCapabilityGate capability={SEARCH_CAPABILITY}>
      <Logs {...props} />
    </NodeCapabilityGate>
  );
}
