import { useQuery } from "@tanstack/react-query";
import { FileText } from "lucide-react";
import { useMemo, useState } from "react";

import { appQuery } from "../../../api/queries/apps";
import { siteLogQuery } from "../../../api/queries/siteLogs";
import type { SiteLogKind } from "../../../api/queries/siteLogs";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { appStatus } from "../../../components/page/status";
import { EmptyState } from "../../../components/ui/EmptyState";
import { LogViewer } from "../../../components/ui/LogViewer";
import type { LogLine } from "../../../components/ui/LogViewer";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatCount } from "../../../lib/format";
import { useLogStream } from "../../../realtime/sockets";
import type { SocketStatus } from "../../../realtime/sockets";
import { levelOf } from "../deployments/buildLog";

/** Most journal lines held on screen; the oldest go first. */
export const LOG_CAP = 10_000;

/** Journal lines asked for when the stream opens. */
const BACKLOG = 200;

/** Which lines the viewer shows: all of them, or only the ones that look like a problem. */
export type LogLevelFilter = "all" | "warnings" | "errors";

function connectionView(t: T, status: SocketStatus): { state: Status; label: string } {
  switch (status) {
    case "connecting":
      return { state: "deploying", label: t("appPages.logs.connecting") };
    case "open":
      return { state: "running", label: t("appPages.logs.live") };
    case "reconnecting":
      return { state: "deploying", label: t("appPages.common.reconnecting") };
    case "closed":
      return { state: "stopped", label: t("appPages.logs.disconnected") };
  }
}

/**
 * A journal line in `short-iso` form: "2026-09-25T21:45:52+0100 web-01 shop[41234]: message".
 * The stamp and the host are systemd's, not the app's: the time goes to the time column, the
 * host (always this machine) is dropped, and the rest stays verbatim from the unit's name on.
 */
const JOURNAL = /^\d{4}-\d{2}-\d{2}T(\d{2}:\d{2}:\d{2})(?:[+-]\d{2}:?\d{2}|Z)? \S+ (.*)$/;

export function journalLine(line: LogLine): LogLine {
  const match = JOURNAL.exec(line.text);
  const text = match?.[2] ?? line.text;
  const level = line.level ?? levelOf(text);
  if (match === null && level === line.level) return line;
  return { ...line, text, ...(match ? { ts: match[1] ?? "" } : {}), ...(level === undefined ? {} : { level }) };
}

/** Whether a line passes the level filter: warnings keep errors too. */
export function passesLevel(line: LogLine, filter: LogLevelFilter): boolean {
  if (filter === "all") return true;
  if (filter === "errors") return line.level === "error";
  return line.level === "error" || line.level === "warn";
}

// A line keeps its identity once read, so the viewer's per-line cache keeps working.
const read = new WeakMap<LogLine, LogLine>();

function withLevel(line: LogLine): LogLine {
  let found = read.get(line);
  if (found === undefined) {
    found = journalLine(line);
    read.set(line, found);
  }
  return found;
}

/**
 * The app's journal as systemd writes it: a backlog, then every new line as it arrives. The
 * stream reconnects by itself; the viewer follows the newest line until the operator scrolls
 * up, `/` searches it, and the level filter narrows it to what looks like a problem. The viewer
 * is as tall as the screen leaves it, so the page itself barely scrolls.
 */
function Journal({ domain, t }: { domain: string; t: T }) {
  const stream = useLogStream(domain, { lines: BACKLOG, cap: LOG_CAP });
  const [level, setLevel] = useState<LogLevelFilter>("all");
  const lines = useMemo(() => stream.lines.map(withLevel), [stream.lines]);
  const shown = useMemo(() => (level === "all" ? lines : lines.filter((line) => passesLevel(line, level))), [lines, level]);
  const connection = connectionView(t, stream.status);

  const levels: readonly { value: LogLevelFilter; label: string }[] = [
    { value: "all", label: t("appPages.logs.levelAll") },
    { value: "warnings", label: t("appPages.logs.levelWarnings") },
    { value: "errors", label: t("appPages.logs.levelErrors") },
  ];

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <StatusPill state={connection.state} label={connection.label} size="sm" />
          <span className="text-12 text-fg-muted tabular-nums">
            {stream.status === "connecting" && lines.length === 0
              ? t("appPages.logs.readingBacklog", { count: BACKLOG })
              : level === "all"
                ? t("appPages.logs.lineCount", { count: lines.length })
                : t("appPages.logs.lineCountFiltered", { shown: formatCount(shown.length, t.locale), total: formatCount(lines.length, t.locale) })}
          </span>
        </div>
        <SegmentedControl<LogLevelFilter> label={t("appPages.logs.levelLabel")} options={levels} value={level} onValueChange={setLevel} />
      </div>

      {stream.error !== null ? (
        <ErrorBlock
          live
          compact
          error={{ detail: stream.error }}
          title={t("appPages.logs.streamFailed")}
          hint={t("appPages.logs.streamFailedHint")}
        />
      ) : null}
      {stream.truncated ? (
        <Notice>
          {t.rich("appPages.logs.truncatedNotice", {
            lines: formatCount(LOG_CAP, t.locale),
            command: <Mono>{`noust logs ${domain} --lines 50000`}</Mono>,
          })}
        </Notice>
      ) : null}

      <div className="h-editor">
        <LogViewer
          lines={shown}
          height="fill"
          pageSearch
          loading={stream.status === "connecting"}
          label={t("appPages.logs.journalLabel", { domain })}
          filename={`${domain}-journal.log`}
          emptyMessage={
            level !== "all" && lines.length > 0
              ? t("appPages.logs.emptyFiltered")
              : stream.status === "open"
                ? t("appPages.logs.emptyOpen")
                : t("appPages.logs.emptyConnecting")
          }
        />
      </div>
      <CommandHint command={`noust logs ${domain} --follow`} label={t("appPages.fromTerminal")} />
    </div>
  );
}

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

/**
 * The end of one of the web server's logs for this site (its requests, or what it complained
 * about), read again every few seconds. What the server returns is already masked: a query
 * string in an access log carries tokens.
 */
function SiteLogs({ domain, kind, t }: { domain: string; kind: SiteLogKind; t: T }) {
  const query = useQuery(siteLogQuery(domain, kind, ""));
  const [level, setLevel] = useState<LogLevelFilter>("all");
  const log = query.data;
  const lines = useMemo<LogLine[]>(
    () =>
      (log?.lines ?? []).map((text, id) => {
        const lineLevel = siteLineLevel(kind, text);
        return lineLevel === undefined ? { id, text } : { id, text, level: lineLevel };
      }),
    [log, kind],
  );
  const shown = useMemo(() => (level === "all" ? lines : lines.filter((line) => passesLevel(line, level))), [lines, level]);
  const levels: readonly { value: LogLevelFilter; label: string }[] = [
    { value: "all", label: t("appPages.logs.levelAll") },
    { value: "warnings", label: kind === "access" ? t("appPages.logs.levelBadRequests") : t("appPages.logs.levelWarnings") },
    { value: "errors", label: kind === "access" ? t("appPages.logs.levelServerErrors") : t("appPages.logs.levelErrors") },
  ];

  if (query.isError && log === undefined) {
    return <ErrorBlock error={query.error} title={t("appPages.logs.siteFailed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />;
  }
  if (log !== undefined && !log.available) {
    return (
      <EmptyState
        variant="firstUse"
        level={3}
        icon={<FileText />}
        title={t("appPages.logs.siteNotWrittenTitle")}
        description={t.rich("appPages.logs.siteNotWritten", { path: <Mono key="path">{log.path ?? ""}</Mono> })}
        command={`noust site logs ${domain} --kind ${kind}`}
      />
    );
  }
  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <span className="text-12 text-fg-muted tabular-nums">
          {log === undefined
            ? t("appPages.logs.siteReading")
            : level === "all"
              ? t("appPages.logs.lineCount", { count: lines.length })
              : t("appPages.logs.lineCountFiltered", { shown: formatCount(shown.length, t.locale), total: formatCount(lines.length, t.locale) })}
        </span>
        <SegmentedControl<LogLevelFilter> label={t("appPages.logs.levelLabel")} options={levels} value={level} onValueChange={setLevel} />
      </div>
      {log?.truncated === true ? (
        <Notice>
          {t.rich("appPages.logs.siteTruncated", {
            shown: formatCount(lines.length, t.locale),
            command: <Mono>{`noust site logs ${domain} --kind ${kind} --contains TEXT`}</Mono>,
          })}
        </Notice>
      ) : null}
      <div className="h-editor">
        <LogViewer
          lines={shown}
          follow
          height="fill"
          pageSearch
          loading={log === undefined}
          label={kind === "access" ? t("appPages.logs.accessLabel", { domain }) : t("appPages.logs.errorLabel", { domain })}
          filename={`${domain}-${kind}.log`}
          emptyMessage={level !== "all" && lines.length > 0 ? t("appPages.logs.emptyFiltered") : t("appPages.logs.siteEmpty")}
        />
      </div>
      <CommandHint command={`noust site logs ${domain} --kind ${kind}`} label={t("appPages.fromTerminal")} />
    </div>
  );
}

type LogSource = "journal" | SiteLogKind;

/** Which log is on screen: the application's own, or one of the web server's for its site. */
function Sources({ source, onChange, withJournal, t }: { source: LogSource; onChange: (source: LogSource) => void; withJournal: boolean; t: T }) {
  const options: { value: LogSource; label: string }[] = [
    ...(withJournal ? [{ value: "journal" as const, label: t("appPages.logs.sourceApp") }] : []),
    { value: "access", label: t("appPages.logs.sourceAccess") },
    { value: "error", label: t("appPages.logs.sourceError") },
  ];
  return <SegmentedControl<LogSource> label={t("appPages.logs.sourceLabel")} options={options} value={source} onValueChange={onChange} className="self-start" />;
}

/**
 * The Logs tab: the unit's journal, followed live, and the web server's access and error logs
 * for the site. A static site has no unit: it has only the web server's, and says so.
 */
export function LogsTab({ domain }: { domain: string }) {
  const t = useT();
  useDocumentTitle(t("appPages.logs.documentTitle", { domain }), 1);
  const app = useQuery(appQuery(domain));
  const [source, setSource] = useState<LogSource>("journal");

  // The layout owns the load failure and the not-found page.
  if (app.data === undefined) {
    return app.isError ? null : (
      <div aria-busy="true" className="flex flex-col gap-3">
        <span className="sr-only">{t("appPages.logs.loadingJournal")}</span>
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-editor w-full rounded-card" />
      </div>
    );
  }

  // A site of the static type has no process even when a unit was left behind for it.
  const isStatic = appStatus(app.data.status).state === "static" || app.data.app_type === "static";
  const shown: LogSource = isStatic && source === "journal" ? "access" : source;

  return (
    <div className="flex flex-col gap-3">
      {isStatic ? (
        <Notice title={t("appPages.logs.staticTitle")}>{t("appPages.logs.staticDescription")}</Notice>
      ) : null}
      <Sources source={shown} onChange={setSource} withJournal={!isStatic} t={t} />
      {shown === "journal" ? <Journal domain={domain} t={t} /> : <SiteLogs domain={domain} kind={shown} t={t} />}
    </div>
  );
}
