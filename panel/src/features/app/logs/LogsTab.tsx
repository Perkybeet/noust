import { useQuery } from "@tanstack/react-query";
import { useMemo } from "react";

import { appQuery } from "../../../api/queries/apps";
import { deploymentsQuery } from "../../../api/queries/deployments";
import { logSearchQuery } from "../../../api/queries/logSearch";
import type { LogSearchParams } from "../../../api/queries/logSearch";
import { useDocumentTitle } from "../../../app/documentTitle";
import { LoadingRegion } from "../../../components/page/LoadingRegion";
import { appStatus } from "../../../components/page/status";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Notice } from "../../../components/ui/Notice";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { parseTimestamp } from "../../../lib/format";
import { LINES } from "../../logs/data";
import { LogToolbar } from "../../logs/LogToolbar";
import type { ToolbarChoice } from "../../logs/LogToolbar";
import { readQuery, renderQuery } from "../../logs/query";
import { AppJournal } from "./AppJournal";
import { appLogsCommand, patchedApp, APP_RANGES } from "./data";
import type { AppLogRange, AppLogSource, AppLogsSearch } from "./data";
import { SiteLogs } from "./SiteLogs";
import { StackLogs, isStack } from "./StackLogs";
import { WindowLines } from "./WindowLines";

export { LOG_CAP } from "./AppJournal";
export { siteLineLevel } from "./SiteLogs";

export interface LogsTabProps {
  domain: string;
  search: AppLogsSearch;
  onSearchChange: (search: AppLogsSearch) => void;
}

/** A search that is never sent: the shape `logSearchQuery` wants while the tab reads something else. */
const NO_SEARCH: LogSearchParams = { q: "", apps: [], since: "1h", level: undefined, limit: LINES };

function sourceChoices(t: T, withJournal: boolean): ToolbarChoice<AppLogSource>[] {
  return [
    ...(withJournal ? [{ value: "app" as const, label: t("appPages.logs.sourceApp") }] : []),
    { value: "access", label: t("appPages.logs.sourceAccess") },
    { value: "error", label: t("appPages.logs.sourceError") },
  ];
}

function rangeChoices(t: T): ToolbarChoice<AppLogRange>[] {
  return APP_RANGES.map((value) => ({ value, label: t(`logs.window.${value}`) }));
}

/**
 * The Logs tab: the unit's journal, followed live, a window of its past, and the web server's
 * access and error logs for the site. A static site has no unit: it has only the web server's,
 * and says so. The toolbar is the Logs page's own (`features/logs/LogToolbar`) and keeps its
 * state in the address: the source, how far back and the words.
 */
export function LogsTab({ domain, search, onSearchChange }: LogsTabProps) {
  const t = useT();
  useDocumentTitle(t("appPages.logs.documentTitle", { domain }), 1);
  const app = useQuery(appQuery(domain));
  const range: AppLogRange = search.since ?? "live";
  const fromDeploy = range === "deploy";
  // The newest deployment, for "since the last deploy": it is read only when that is what was asked.
  const newest = useQuery({ ...deploymentsQuery({ domain, limit: 1 }), enabled: fromDeploy });
  const deploy = newest.data?.items[0];
  const deployedAt = parseTimestamp(deploy?.started_at);

  // A site of the static type has no process even when a unit was left behind for it.
  const isStatic = app.data !== undefined && (appStatus(app.data.status).state === "static" || app.data.app_type === "static");
  const source: AppLogSource = search.source ?? (isStatic ? "access" : "app");
  const windowed = source === "app" && range !== "live";
  const ready = !fromDeploy || deployedAt !== null;

  const params: LogSearchParams = windowed
    ? { q: search.q ?? "", apps: [domain], since: fromDeploy ? String(Math.floor((deployedAt?.getTime() ?? 0) / 1000)) : range, level: undefined, limit: LINES }
    : NO_SEARCH;
  const found = useQuery({ ...logSearchQuery(params), enabled: windowed && ready });

  // What a followed stream and the end of a file are filtered by is read here, with the grammar the
  // server reads; a window is judged by the server, which writes the query back.
  const read = useMemo(() => readQuery(search.q ?? ""), [search.q]);
  const filter = !windowed && read.ok && read.node.kind !== "any" ? read.node : null;
  const understood = windowed ? found.data?.understood : filter !== null ? renderQuery(filter) : undefined;

  // The layout owns the load failure and the not-found page.
  if (app.data === undefined) {
    return app.isError ? null : (
      <LoadingRegion label={t("appPages.logs.loadingJournal")}>
        <SkeletonCard lines={6} />
      </LoadingRegion>
    );
  }

  const command = appLogsCommand(domain, source, search, deployedAt === null ? null : Math.floor(deployedAt.getTime() / 1000));
  const stack = isStack(app.data.app_type ?? "");

  return (
    <div className="flex flex-col gap-4">
      {isStatic ? <Notice title={t("appPages.logs.staticTitle")}>{t("appPages.logs.staticDescription")}</Notice> : null}
      <LogToolbar<AppLogRange, AppLogSource>
        label={t("logs.filterLabel")}
        query={{
          value: search.q ?? "",
          onChange: (q) => {
            onSearchChange(patchedApp(search, { q }));
          },
          label: t("logs.searchLabel"),
          placeholder: t("logs.searchPlaceholder"),
        }}
        sources={{
          label: t("appPages.logs.sourceLabel"),
          value: source,
          options: sourceChoices(t, !isStatic),
          onChange: (next) => {
            onSearchChange(patchedApp(search, { source: next === "app" ? undefined : next }));
          },
        }}
        range={{
          label: t("logs.windowLabel"),
          value: range,
          options: rangeChoices(t),
          onChange: (since) => {
            onSearchChange(patchedApp(search, { since }));
          },
          // The end of a web server's file is its newest lines: there is no past to ask for.
          hidden: source !== "app",
        }}
        understood={understood}
        error={windowed || read.ok ? undefined : read.error.message}
      />
      {source !== "app" ? (
        <SiteLogs domain={domain} kind={source} filter={filter} command={command ?? ""} />
      ) : windowed ? (
        fromDeploy && newest.data !== undefined && deploy === undefined ? (
          <EmptyState variant="inline" title={t("logs.app.noDeploy")} />
        ) : (
          <WindowLines domain={domain} query={found} deploy={deploy !== undefined ? { id: deploy.id } : undefined} command={command ?? undefined} />
        )
      ) : stack ? (
        // A stack's unit only starts its containers: what they printed is the tab, and the journal one choice of it.
        <StackLogs domain={domain} filter={filter} unit={<AppJournal domain={domain} filter={filter} command={command ?? ""} />} />
      ) : (
        <AppJournal domain={domain} filter={filter} command={command ?? ""} />
      )}
    </div>
  );
}
