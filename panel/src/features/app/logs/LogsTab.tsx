import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { FileText } from "lucide-react";
import { useMemo } from "react";

import { appQuery } from "../../../api/queries/apps";
import { useDocumentTitle } from "../../../app/documentTitle";
import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { appStatus } from "../../../components/page/status";
import { EmptyState } from "../../../components/ui/EmptyState";
import { LogViewer } from "../../../components/ui/LogViewer";
import type { LogLine } from "../../../components/ui/LogViewer";
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
 * up, and `/` searches it.
 */
function Journal({ domain, failed, t }: { domain: string; failed: boolean; t: T }) {
  const stream = useLogStream(domain, { lines: BACKLOG, cap: LOG_CAP });
  const lines = useMemo(() => stream.lines.map(withLevel), [stream.lines]);
  const connection = connectionView(t, stream.status);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-x-4 gap-y-2">
        <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
          <StatusPill state={connection.state} label={connection.label} size="sm" />
          <span className="text-12 text-fg-muted">
            {stream.status === "connecting" && lines.length === 0
              ? t("appPages.logs.readingBacklog", { count: BACKLOG })
              : t("appPages.logs.lineCount", { count: lines.length })}
          </span>
          {failed ? (
            <span className="text-12 text-fg-muted">
              {t.rich("appPages.logs.unitFailedHint", {
                diagnose: (
                  <Link
                    to="/apps/$domain/diagnose"
                    params={{ domain }}
                    className="rounded-[4px] font-medium text-accent-fg hover:underline hover:underline-offset-2 focus-visible:outline-2 focus-visible:outline-focus"
                  >
                    {t("nav.appTabs.diagnose.label")}
                  </Link>
                ),
              })}
            </span>
          ) : null}
        </div>
        <CommandHint command={`noust logs ${domain} --follow`} label={t("appPages.fromTerminal")} />
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
        <p className="rounded-control border border-border bg-bg-sunken px-3 py-2 text-12 text-fg-muted">
          {t.rich("appPages.logs.truncatedNotice", {
            lines: formatCount(LOG_CAP, t.locale),
            command: <code translate="no" className="text-fg">{`noust logs ${domain} --lines 50000`}</code>,
          })}
        </p>
      ) : null}

      <div className="h-[max(24rem,calc(100dvh-22rem))]">
        <LogViewer
          lines={lines}
          height="fill"
          pageSearch
          label={t("appPages.logs.journalLabel", { domain })}
          filename={`${domain}-journal.log`}
          emptyMessage={stream.status === "open" ? t("appPages.logs.emptyOpen") : t("appPages.logs.emptyConnecting")}
        />
      </div>
    </div>
  );
}

/** The Logs tab: the unit's journal, followed live. A static site has no unit, and says so. */
export function LogsTab({ domain }: { domain: string }) {
  const t = useT();
  useDocumentTitle(t("appPages.logs.documentTitle", { domain }), 1);
  const app = useQuery(appQuery(domain));

  // The layout owns the load failure and the not-found page.
  if (app.data === undefined) {
    return app.isError ? null : (
      <div aria-busy="true" className="flex flex-col gap-3">
        <span className="sr-only">{t("appPages.logs.loadingJournal")}</span>
        <Skeleton className="h-6 w-40" />
        <Skeleton className="h-[24rem] w-full rounded-card" />
      </div>
    );
  }

  // A site of the static type has no process even when a unit was left behind for it.
  if (appStatus(app.data.status).state === "static" || app.data.app_type === "static") {
    return (
      <EmptyState
        level={2}
        icon={<FileText />}
        title={t("appPages.logs.staticTitle")}
        description={t("appPages.logs.staticDescription")}
        command={`tail -f /var/log/nginx/access.log`}
        className="py-16"
      />
    );
  }

  return <Journal domain={domain} failed={appStatus(app.data.status).state === "failed"} t={t} />;
}
