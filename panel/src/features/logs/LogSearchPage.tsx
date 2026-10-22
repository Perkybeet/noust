/**
 * Logs (T1): one place to search the logs, over two sources. The applications' (the journal's
 * lines and the containers' output of every application, newest last) and the system's (the whole
 * journal, which was the Server area's Logs tab until 4.0). The words, the source, the range, the
 * application or the service are search params, so a search can be shared. A team sees only its
 * own applications' lines; the system's journal is the machine's and needs its permission.
 */

import { Suspense, lazy } from "react";

import { CommandHint } from "../../components/page/CommandHint";
import { ListPage } from "../../components/page/ListPage";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { SkeletonCard } from "../../components/ui/Skeleton";
import { TextLink } from "../../components/ui/TextLink";
import { useT } from "../../i18n";
import { AppsLogs } from "./AppsLogs";
import { searchCommand } from "./data";
import type { LogsSearch } from "./data";

// The system's source brings the timeline and the context drawer: only whoever asks for it loads them.
const SystemLogs = lazy(() => import("../server/logs/SystemLogs").then((module) => ({ default: module.SystemLogs })));

export interface LogSearchPageProps {
  search: LogsSearch;
  /** `push` adds a history entry: opening a line is a place Back should leave. */
  onSearchChange: (search: LogsSearch, options?: { push?: boolean }) => void;
}

export function LogSearchPage({ search, onSearchChange }: LogSearchPageProps) {
  const t = useT();
  return (
    <ListPage
      header={{
        title: t("logs.title"),
        description: t("logs.description"),
        secondaryActions: <TextLink to="/settings/log-shipping">{t("logs.retentionLink")}</TextLink>,
      }}
      footer={<CommandHint command={searchCommand(search)} label={t("common.words.fromTerminal")} />}
    >
      {search.source === "system" ? (
        <Suspense
          fallback={
            <LoadingRegion label={t("logs.loading")}>
              <SkeletonCard lines={6} />
            </LoadingRegion>
          }
        >
          <SystemLogs search={search} onSearchChange={onSearchChange} />
        </Suspense>
      ) : (
        <AppsLogs search={search} onSearchChange={onSearchChange} />
      )}
    </ListPage>
  );
}
