import { useQuery } from "@tanstack/react-query";
import { FileText } from "lucide-react";
import { useMemo } from "react";

import { siteLogQuery } from "../../../api/queries/siteLogs";
import type { SiteLog, SiteLogKind } from "../../../api/queries/siteLogs";
import { CommandHint } from "../../../components/page/CommandHint";
import { QueryState } from "../../../components/page/QueryState";
import { EmptyState } from "../../../components/ui/EmptyState";
import { LogViewer } from "../../../components/ui/LogViewer";
import type { LogLine } from "../../../components/ui/LogViewer";
import { Mono } from "../../../components/ui/Mono";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import { formatCount } from "../../../lib/format";
import { levelOf } from "../deployments/buildLog";
import { keeps, priorityOfViewerLevel } from "../../logs/query";
import type { QueryNode } from "../../logs/query";

/**
 * The status code of an access-log line: the three digits after the quoted request. A server
 * error is an error and a client error a warning, so the level filter finds the 502 among
 * thousands of 200s.
 */
const ACCESS_STATUS = /"\s(\d{3})\s/;

/** The level of a web-server log line: by its status for an access log, by its own word for an error log. */
export function siteLineLevel(kind: SiteLogKind, text: string): LogLine["level"] | undefined {
  if (kind === "error") return levelOf(text);
  const status = ACCESS_STATUS.exec(text)?.[1];
  if (status === undefined) return undefined;
  if (status.startsWith("5")) return "error";
  return status.startsWith("4") ? "warn" : undefined;
}

export interface SiteLogsProps {
  domain: string;
  kind: SiteLogKind;
  /** What the query asked, read by the tab: the end of the file is filtered here, line by line. */
  filter: QueryNode | null;
  command: string;
}

/**
 * The end of one of the web server's logs for this site (its requests, or what it complained
 * about), read again every few seconds. What the server returns is already masked: a query
 * string in an access log carries tokens.
 */
export function SiteLogs({ domain, kind, filter, command }: SiteLogsProps) {
  const t = useT();
  const query = useQuery(siteLogQuery(domain, kind, ""));
  return (
    <QueryState query={query} label={t("logs.app.siteNoun")} skeleton={<SkeletonCard lines={6} />}>
      {(log) => <SiteLines domain={domain} kind={kind} log={log} filter={filter} command={command} />}
    </QueryState>
  );
}

function SiteLines({ domain, kind, log, filter, command }: SiteLogsProps & { log: SiteLog }) {
  const t = useT();
  const lines = useMemo<LogLine[]>(
    () =>
      (log.lines ?? []).flatMap((text, id) => {
        const lineLevel = siteLineLevel(kind, text);
        if (filter !== null && !keeps(filter, { message: text, priority: priorityOfViewerLevel(lineLevel) })) return [];
        return [lineLevel === undefined ? { id, text } : { id, text, level: lineLevel }];
      }),
    [log.lines, kind, filter],
  );
  // A request's level is its status, so the viewer's chips say what they filter: 4xx and 5xx.
  const levelWords = kind === "access" ? { warn: t("appPages.logs.accessWarn"), error: t("appPages.logs.accessError") } : undefined;

  if (!log.available) {
    return (
      <EmptyState
        variant="firstUse"
        level={3}
        icon={<FileText />}
        title={t("appPages.logs.siteNotWrittenTitle")}
        description={t.rich("appPages.logs.siteNotWritten", { path: <Mono key="path">{log.path ?? ""}</Mono> })}
        command={command}
      />
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-4 gap-y-1">
        <span className="text-12 text-fg-muted tabular-nums">{t("appPages.logs.lineCount", { count: lines.length })}</span>
        {log.truncated ? (
          <span className="max-w-measure text-12 text-pretty text-fg-muted">
            {t.rich("appPages.logs.siteTruncated", {
              shown: formatCount(log.lines?.length ?? 0, t.locale),
              command: <Mono>{`noust site logs ${domain} --kind ${kind} --contains TEXT`}</Mono>,
            })}
          </span>
        ) : null}
      </div>
      <div className="h-editor">
        <LogViewer
          lines={lines}
          follow
          height="fill"
          searchable={false}
          {...(levelWords !== undefined ? { levelWords } : {})}
          label={kind === "access" ? t("appPages.logs.accessLabel", { domain }) : t("appPages.logs.errorLabel", { domain })}
          filename={`${domain}-${kind}.log`}
          emptyMessage={t("appPages.logs.siteEmpty")}
        />
      </div>
      <CommandHint command={command} label={t("appPages.fromTerminal")} />
    </div>
  );
}
