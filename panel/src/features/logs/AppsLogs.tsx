/**
 * Logs over the applications: the journal's lines and the containers' output of every application
 * the principal sees (or of one), newest last, with credentials already masked by the server. The
 * toolbar's words, range and application are the address's, so a search can be shared. With
 * "Live" the same question is asked again every few seconds and the viewer follows the end.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { isApiError } from "../../api/client";
import { appsQuery } from "../../api/queries/apps";
import { logSearchQuery } from "../../api/queries/logSearch";
import { ErrorBlock, QueryState } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { LogViewer } from "../../components/ui/LogViewer";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { useHasFleet, useServerList } from "../../nodes/servers";
import { ViewNotices } from "../operation/ViewNotices";
import { useView } from "../operation/views";
import { LIVE_MS, fleetViewerLines, isLive, paramsOf, rangeOf, viewerLines } from "./data";
import type { LogsSearch } from "./data";
import { LogToolbar } from "./LogToolbar";
import { patched, rangeChoices, sourceChoices, switchedTo } from "./sources";
import type { Patch } from "./sources";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/** Select's value for "no filter": an option needs a value. */
const ALL = "__all";
/** The scope select's values that are not a server's name (a name is never `@`-prefixed). */
const THIS_SERVER = "@this";
const EVERY_SERVER = "@all";

/** Lines asked of each server when every server is searched. */
const FLEET_LINES = 100;

export interface AppsLogsProps {
  search: LogsSearch;
  onSearchChange: (search: LogsSearch) => void;
}

/** The viewer while the first answer is on its way, with the room the answer will take. */
function LoadingViewer({ label }: { label: string }) {
  return (
    <div className="flex h-editor min-h-0 min-w-0 flex-col">
      <LogViewer lines={[]} height="fill" searchable={false} loading label={label} emptyMessage={label} />
    </div>
  );
}

export function AppsLogs({ search, onSearchChange }: AppsLogsProps) {
  const t = useT();
  const hasFleet = useHasFleet();
  const servers = useServerList();
  const fleet = hasFleet && search.scope === "fleet";
  const live = isLive(search);
  const apps = useQuery(appsQuery());
  const params = paramsOf(search);
  const result = useQuery({ ...logSearchQuery(params), enabled: !fleet, refetchInterval: live ? LIVE_MS : false });
  const across = useView(
    "logs",
    { nodes: search.server !== undefined ? [search.server] : [], q: params.q, since: params.since, ...(params.level !== undefined ? { level: params.level } : {}), limit: FLEET_LINES },
    { live: live ? LIVE_MS : false, enabled: fleet },
  );
  // Newest first from the server; a viewer reads oldest first and follows the end.
  const lines = useMemo(() => (result.data === undefined ? [] : viewerLines([...result.data.lines].reverse())), [result.data]);
  const fleetLines = useMemo(() => (across.data === undefined ? [] : fleetViewerLines(across.data.items)), [across.data]);
  const fleetNotes = (across.data?.items ?? []).filter((row) => row["type"] === "note");

  const set = (patch: Patch): void => {
    onSearchChange(patched(search, patch));
  };

  const names = (apps.data?.apps ?? []).map((app) => app.domain);
  const appOptions = [
    { value: ALL, label: t("logs.allApps") },
    ...names.map((domain) => ({ value: domain, label: domain })),
    // An application named in the address that the list does not have (yet) still shows as chosen.
    ...(search.app !== undefined && !names.includes(search.app) ? [{ value: search.app, label: search.app }] : []),
  ];
  // A query the server cannot read is the question's fault: its own words, verbatim, beside the box.
  const refused = isApiError(result.error) && result.error.status === 400;
  const found = result.data;
  const filtered = search.q !== undefined || search.app !== undefined || search.level !== undefined || search.since !== undefined || search.scope !== undefined;
  // Which servers the search reads: this one, all of them or one by name, said in one control.
  const scopeValue = !fleet ? THIS_SERVER : (search.server ?? EVERY_SERVER);
  const scopeOptions = [
    { value: THIS_SERVER, label: servers.hostname === null ? t("fleetCompare.logs.thisServer") : t("operation.scope.central", { name: servers.hostname }) },
    { value: EVERY_SERVER, label: t("operation.scope.all") },
    ...servers.nodes.map((node) => ({ value: node.name, label: node.name })),
  ];

  const toolbar = (
    <LogToolbar
      label={t("logs.filterLabel")}
      query={{
        value: search.q ?? "",
        onChange: (q) => {
          set({ q });
        },
        label: t("logs.searchLabel"),
        placeholder: t("logs.searchPlaceholder"),
      }}
      sources={{
        label: t("logs.sourceLabel"),
        value: "apps",
        options: sourceChoices(t),
        onChange: (source) => {
          onSearchChange(switchedTo(search, source));
        },
      }}
      picker={
        <>
          {hasFleet ? (
            <Select
              aria-label={t("fleetCompare.logs.scopeLabel")}
              value={scopeValue}
              onValueChange={(next) => {
                // The servers' answer is not narrowed to one application: the choice goes with the scope.
                if (next === THIS_SERVER) set({ scope: undefined, server: undefined });
                else set({ scope: "fleet", server: next === EVERY_SERVER ? undefined : next, app: undefined });
              }}
              options={scopeOptions}
              className="min-w-44"
            />
          ) : null}
          <Select
            aria-label={t("logs.appLabel")}
            disabled={fleet}
            value={search.app ?? ALL}
            onValueChange={(app) => {
              set({ app: app === ALL ? undefined : app });
            }}
            options={appOptions}
            mono
            className="w-56"
          />
        </>
      }
      range={{
        label: t("logs.windowLabel"),
        value: rangeOf(search),
        options: rangeChoices(t, "apps"),
        onChange: (since) => {
          set({ since });
        },
      }}
      understood={fleet ? undefined : found?.understood}
    />
  );

  if (fleet) {
    return (
      <div className="flex min-w-0 flex-col gap-4">
        {toolbar}
        <ViewNotices view={across.data} resource="logs" />
        <QueryState
          query={across}
          label={t("fleetCompare.logs.label")}
          skeleton={<LoadingViewer label={t("logs.loading")} />}
        >
          {() => (
            <>
              <p role="status" className="text-13 text-fg-muted">
                {fleetLines.length === 0
                  ? t("fleetCompare.logs.nothing")
                  : `${t("fleetCompare.logs.summary", { count: fleetLines.length, servers: across.data?.nodes.length ?? 0 })} ${t("fleetCompare.logs.perServer", { limit: FLEET_LINES })}`}
              </p>
              {fleetNotes.length > 0 ? (
                <ul className="flex flex-col gap-1 text-12 text-fg-muted">
                  {fleetNotes.map((row) => (
                    <li key={`${String(row["node"])}:${String(row["message"])}`} translate="no">
                      {`${String(row["node"])}: ${String(row["message"])}`}
                    </li>
                  ))}
                </ul>
              ) : null}
              <div key={rangeOf(search)} className="flex h-editor min-h-0 min-w-0 flex-col">
                <LogViewer lines={fleetLines} height="fill" follow searchable={false} label={t("logs.viewerLabel")} filename="logs.log" emptyMessage={t("logs.empty")} />
              </div>
            </>
          )}
        </QueryState>
      </div>
    );
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {toolbar}
      {refused ? (
        <ErrorBlock
          error={result.error}
          title={t("logs.refusedTitle")}
          onRetry={() => {
            void result.refetch();
          }}
          retrying={result.isRefetching}
        />
      ) : (
        <QueryState query={result} label={t("logs.noun")} skeleton={<LoadingViewer label={t("logs.loading")} />}>
          {(answer) => (
            <div className="flex min-w-0 flex-col gap-4">
              <div className="flex min-w-0 flex-col gap-1">
                <p role="status" className="text-13 text-fg-muted">
                  {answer.lines.length === 0
                    ? t("logs.nothing", { scanned: answer.scanned })
                    : t("logs.summary", { shown: answer.lines.length, matched: answer.matched, scanned: answer.scanned })}
                </p>
                {answer.truncated ? <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("logs.truncated", { scanned: answer.scanned })}</p> : null}
              </div>
              {answer.notes.length > 0 ? (
                <ul className="flex flex-col gap-1 text-12 text-fg-muted">
                  {answer.notes.map((note) => (
                    <li key={note} translate="no">
                      {note}
                    </li>
                  ))}
                </ul>
              ) : null}
              {answer.lines.length === 0 && answer.apps.length === 0 && search.q === undefined ? (
                <EmptyState variant="firstUse" icon={<OBJECT_ICONS.log />} title={t("logs.noAppsTitle")} description={t("logs.noAppsDescription")} />
              ) : (
                // Keyed by the range: choosing "Live" starts the viewer following the end again.
                <div key={rangeOf(search)} className="flex h-editor min-h-0 min-w-0 flex-col">
                  <LogViewer
                    lines={lines}
                    height="fill"
                    follow
                    // The question above asks the server itself, past the lines on screen: one search box.
                    searchable={false}
                    label={t("logs.viewerLabel")}
                    filename="logs.log"
                    emptyMessage={t("logs.empty")}
                  />
                </div>
              )}
              {filtered ? (
                <div>
                  <Button
                    size="sm"
                    variant="ghost"
                    icon={<ICONS.close aria-hidden="true" />}
                    onClick={() => {
                      onSearchChange({});
                    }}
                  >
                    {t("logs.clear")}
                  </Button>
                </div>
              ) : null}
            </div>
          )}
        </QueryState>
      )}
    </div>
  );
}
