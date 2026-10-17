/**
 * Logs (T1): one search over the logs of every application at once - the journal's lines and the
 * containers' output, newest first, with credentials already masked by the server. The words,
 * the window, the level and the application are search params, so a search can be shared. A
 * team sees only its own applications' lines.
 */

import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { ScrollText } from "lucide-react";
import { useMemo } from "react";

import { isApiError } from "../../api/client";
import { appsQuery } from "../../api/queries/apps";
import { fleetLogsQuery } from "../../api/queries/fleetCompare";
import { logSearchQuery } from "../../api/queries/logSearch";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { LogViewer } from "../../components/ui/LogViewer";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { useHasFleet } from "../../nodes/servers";
import { PartialNotice } from "../fleet/parts";
import { DEFAULT_WINDOW, LEVELS, WINDOWS, fleetViewerLines, paramsOf, searchCommand, viewerLines } from "./data";
import type { LogLevel, LogWindow, LogsScope, LogsSearch } from "./data";

/** Select's value for "no filter": an option needs a value. */
const ALL = "__all";

export interface LogSearchPageProps {
  search: LogsSearch;
  onSearchChange: (search: LogsSearch) => void;
}

function windowOptions(t: T): { value: LogWindow; label: string }[] {
  return WINDOWS.map((value) => ({ value, label: t(`logSearch.window.${value}`) }));
}

function levelOptions(t: T): { value: LogLevel | typeof ALL; label: string }[] {
  return [{ value: ALL, label: t("logSearch.level.all") }, ...LEVELS.map((value) => ({ value, label: t(`logSearch.level.${value}`) }))];
}

/** A change to the question: a key set to undefined is cleared. */
type Patch = { [K in keyof LogsSearch]?: LogsSearch[K] | undefined };

/** Lines asked of each server when every server is searched. */
const FLEET_LINES = 100;

export function LogSearchPage({ search, onSearchChange }: LogSearchPageProps) {
  const t = useT();
  const hasFleet = useHasFleet();
  const fleet = hasFleet && search.scope === "fleet";
  const apps = useQuery(appsQuery());
  const params = paramsOf(search);
  const result = useQuery({ ...logSearchQuery(params), enabled: !fleet });
  const across = useQuery({ ...fleetLogsQuery({ q: params.q, since: params.since, level: params.level, limit: FLEET_LINES }), enabled: fleet });
  // Newest first from the server; a viewer reads oldest first and follows the end.
  const lines = useMemo(() => (result.data === undefined ? [] : viewerLines([...result.data.lines].reverse())), [result.data]);
  const fleetLines = useMemo(() => (across.data === undefined ? [] : fleetViewerLines(across.data.items)), [across.data]);
  const fleetNotes = (across.data?.items ?? []).filter((row) => row["type"] === "note");

  const set = (patch: Patch): void => {
    const next: Patch = { ...search, ...patch };
    const clean: LogsSearch = {};
    if (next.q) clean.q = next.q;
    if (next.app) clean.app = next.app;
    if (next.scope) clean.scope = next.scope;
    if (next.since) clean.since = next.since;
    if (next.level) clean.level = next.level;
    onSearchChange(clean);
  };

  const names = (apps.data?.apps ?? []).map((app) => app.domain);
  const appOptions = [
    { value: ALL, label: t("logSearch.allApps") },
    ...names.map((domain) => ({ value: domain, label: domain })),
    // An application named in the address that the list does not have (yet) still shows as chosen.
    ...(search.app !== undefined && !names.includes(search.app) ? [{ value: search.app, label: search.app }] : []),
  ];
  const refused = result.isError && isApiError(result.error) && result.error.status === 400;
  const found = result.data;

  return (
    <ListPage
      header={{
        title: t("logSearch.title"),
        description: t("logSearch.description"),
        secondaryActions: (
          <Link to="/settings/log-shipping" className="text-13 text-accent hover:underline">
            {t("logSearch.retentionLink")}
          </Link>
        ),
      }}
      filters={
        <FilterBar
          label={t("logSearch.filterLabel")}
          search={{
            label: t("logSearch.searchLabel"),
            placeholder: t("logSearch.searchPlaceholder"),
            value: search.q ?? "",
            onChange: (q) => {
              set({ q: q === "" ? undefined : q });
            },
          }}
          filters={
            <>
              {hasFleet ? (
                <SegmentedControl<"server" | LogsScope>
                  label={t("fleetCompare.logs.scopeLabel")}
                  value={fleet ? "fleet" : "server"}
                  onValueChange={(scope) => {
                    set({ scope: scope === "fleet" ? "fleet" : undefined });
                  }}
                  options={[
                    { value: "server", label: t("fleetCompare.logs.thisServer") },
                    { value: "fleet", label: t("fleetCompare.logs.everyServer") },
                  ]}
                />
              ) : null}
              <Select
                aria-label={t("logSearch.appLabel")}
                disabled={fleet}
                value={search.app ?? ALL}
                onValueChange={(app) => {
                  set({ app: app === ALL ? undefined : app });
                }}
                options={appOptions}
                mono
                className="w-56"
              />
              <Select
                aria-label={t("logSearch.levelLabel")}
                value={search.level ?? ALL}
                onValueChange={(level) => {
                  set({ level: level === ALL ? undefined : (level) });
                }}
                options={levelOptions(t)}
              />
              <SegmentedControl<LogWindow>
                label={t("logSearch.windowLabel")}
                value={search.since ?? DEFAULT_WINDOW}
                onValueChange={(since) => {
                  set({ since: since === DEFAULT_WINDOW ? undefined : since });
                }}
                options={windowOptions(t)}
              />
            </>
          }
        />
      }
      footer={<CommandHint command={searchCommand(search)} label={t("logSearch.fromTerminal")} />}
    >
      {fleet ? (
        <div className="flex min-w-0 flex-col gap-4">
          <PartialNotice view={across.data} />
          {across.isError && across.data === undefined ? (
            <ErrorBlock
              error={across.error}
              title={t("fleetCompare.logs.loadFailed")}
              onRetry={() => {
                void across.refetch();
              }}
              retrying={across.isRefetching}
            />
          ) : across.data === undefined ? (
            <div aria-busy="true" className="flex h-editor min-h-0 min-w-0 flex-col">
              <span className="sr-only">{t("logSearch.loading")}</span>
              <LogViewer lines={[]} height="fill" searchable={false} loading label={t("logSearch.viewerLabel")} emptyMessage={t("logSearch.loading")} />
            </div>
          ) : (
            <>
              <p role="status" className="text-13 text-fg-muted">
                {fleetLines.length === 0
                  ? t("fleetCompare.logs.nothing")
                  : `${t("fleetCompare.logs.summary", { count: fleetLines.length, servers: across.data.nodes.length })} ${t("fleetCompare.logs.perServer", { limit: FLEET_LINES })}`}
              </p>
              {search.app !== undefined ? <Notice tone="info">{t("fleetCompare.logs.appNote")}</Notice> : null}
              {fleetNotes.length > 0 ? (
                <ul className="flex flex-col gap-1 text-12 text-fg-muted">
                  {fleetNotes.map((row) => (
                    <li key={`${String(row["node"])}:${String(row["message"])}`} translate="no">
                      {`${String(row["node"])}: ${String(row["message"])}`}
                    </li>
                  ))}
                </ul>
              ) : null}
              <div className="flex h-editor min-h-0 min-w-0 flex-col">
                <LogViewer lines={fleetLines} height="fill" follow searchable={false} label={t("logSearch.viewerLabel")} filename="logs.log" emptyMessage={t("logSearch.empty")} />
              </div>
            </>
          )}
        </div>
      ) : result.isError && result.data === undefined ? (
        // A query the server cannot read is the question's fault: its own words, verbatim, beside the box.
        <ErrorBlock
          error={result.error}
          title={refused ? t("logSearch.refusedTitle") : t("logSearch.loadFailed")}
          onRetry={() => {
            void result.refetch();
          }}
          retrying={result.isRefetching}
        />
      ) : found === undefined ? (
        <div aria-busy="true" className="flex h-editor min-h-0 min-w-0 flex-col">
          <span className="sr-only">{t("logSearch.loading")}</span>
          <LogViewer lines={[]} height="fill" searchable={false} loading label={t("logSearch.viewerLabel")} emptyMessage={t("logSearch.loading")} />
        </div>
      ) : (
        <div className="flex min-w-0 flex-col gap-4">
          <p role="status" className="text-13 text-fg-muted">
            {found.lines.length === 0
              ? t("logSearch.nothing", { scanned: found.scanned })
              : t("logSearch.summary", { shown: found.lines.length, matched: found.matched, scanned: found.scanned })}
          </p>
          {found.truncated ? <Notice tone="info">{t("logSearch.truncated", { scanned: found.scanned })}</Notice> : null}
          {found.notes.length > 0 ? (
            <ul className="flex flex-col gap-1 text-12 text-fg-muted">
              {found.notes.map((note) => (
                <li key={note} translate="no">
                  {note}
                </li>
              ))}
            </ul>
          ) : null}
          {found.lines.length === 0 && found.apps.length === 0 && search.q === undefined ? (
            <EmptyState variant="firstUse" icon={<ScrollText />} title={t("logSearch.noAppsTitle")} description={t("logSearch.noAppsDescription")} />
          ) : (
            <div className="flex h-editor min-h-0 min-w-0 flex-col">
              <LogViewer
                lines={lines}
                height="fill"
                follow
                // The question above asks the server itself, past the lines on screen: one search box.
                searchable={false}
                label={t("logSearch.viewerLabel")}
                filename="logs.log"
                emptyMessage={t("logSearch.empty")}
              />
            </div>
          )}
          {search.q !== undefined || search.app !== undefined || search.level !== undefined || search.since !== undefined || search.scope !== undefined ? (
            <div>
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  onSearchChange({});
                }}
              >
                {t("logSearch.clear")}
              </Button>
            </div>
          ) : null}
        </div>
      )}
    </ListPage>
  );
}
