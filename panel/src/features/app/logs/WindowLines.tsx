import { useMemo } from "react";

import type { LogSearchResult } from "../../../api/queries/logSearch";
import { CommandHint } from "../../../components/page/CommandHint";
import { QueryState } from "../../../components/page/QueryState";
import type { QueryLike } from "../../../components/page/QueryState";
import { LogViewer } from "../../../components/ui/LogViewer";
import type { LogLine } from "../../../components/ui/LogViewer";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { viewerLines } from "../../logs/data";

export interface WindowLinesProps {
  domain: string;
  query: QueryLike<LogSearchResult>;
  /** The deployment the window starts at, when it starts at one: its mark opens the output. */
  deploy?: { id: number } | undefined;
  /** The command that reads the same from a terminal, when there is one. */
  command?: string | undefined;
}

/**
 * A window of the past of this application, asked of the server: the lines that match the query,
 * oldest first, with the server's reading of the query above them and how much it examined. A
 * window that starts at a deployment says so on its first line, so the output before and after it
 * is told apart.
 */
export function WindowLines({ domain, query, deploy, command }: WindowLinesProps) {
  const t = useT();
  const answer = query.data;
  // Newest first from the server; a viewer reads oldest first.
  const lines = useMemo<LogLine[]>(() => {
    const read = answer === undefined ? [] : viewerLines([...answer.lines].reverse());
    return deploy === undefined ? read : [{ id: -1, level: "info", text: `—— ${t("logs.app.deployMark", { id: deploy.id })} ——` }, ...read];
  }, [answer, deploy, t]);

  return (
    <QueryState query={query} label={t("logs.app.noun")} skeleton={<SkeletonCard lines={6} />}>
      {(found) => (
        <div className="flex flex-col gap-3">
          <div className="flex flex-col gap-1">
            <p role="status" className="text-13 text-fg-muted">
              {found.lines.length === 0
                ? t("logs.nothing", { scanned: found.scanned })
                : t("logs.summary", { shown: found.lines.length, matched: found.matched, scanned: found.scanned })}
            </p>
            {found.truncated ? <p className="max-w-measure text-12 text-pretty text-fg-muted">{t("logs.truncated", { scanned: found.scanned })}</p> : null}
          </div>
          {found.notes.length > 0 ? (
            <ul className="flex flex-col gap-1 text-12 text-fg-muted">
              {found.notes.map((note) => (
                <li key={note} translate="no">
                  {note}
                </li>
              ))}
            </ul>
          ) : null}
          <div className="h-editor">
            <LogViewer
              lines={lines}
              follow
              height="fill"
              searchable={false}
              label={t("appPages.logs.journalLabel", { domain })}
              filename={`${domain}-logs.log`}
              emptyMessage={t("logs.empty")}
            />
          </div>
          {command !== undefined ? <CommandHint command={command} label={t("appPages.fromTerminal")} /> : null}
        </div>
      )}
    </QueryState>
  );
}
