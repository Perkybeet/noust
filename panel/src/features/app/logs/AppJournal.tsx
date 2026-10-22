import { useMemo } from "react";

import { CommandHint } from "../../../components/page/CommandHint";
import { ErrorBlock } from "../../../components/page/QueryState";
import { LogViewer } from "../../../components/ui/LogViewer";
import { Mono } from "../../../components/ui/Mono";
import { StatusPill } from "../../../components/ui/StatusPill";
import type { Status } from "../../../components/ui/StatusPill";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatCount } from "../../../lib/format";
import { useLogStream } from "../../../realtime/sockets";
import type { SocketStatus } from "../../../realtime/sockets";
import { keeps, priorityOfViewerLevel } from "../../logs/query";
import type { QueryNode } from "../../logs/query";
import { withLevel } from "./journalLines";

/** Most journal lines held on screen; the oldest go first. */
export const LOG_CAP = 10_000;

/** Journal lines asked for when the stream opens. */
const BACKLOG = 200;

function connectionView(t: T, status: SocketStatus): { state: Status; label: string } {
  switch (status) {
    case "connecting":
      return { state: "deploying", label: t("appPages.logs.connecting") };
    case "open":
      // The word "Live" is the toolbar's range; this pill says only whether the stream is connected.
      return { state: "running", label: t("logs.app.connected") };
    case "reconnecting":
      return { state: "deploying", label: t("appPages.common.reconnecting") };
    case "closed":
      return { state: "stopped", label: t("appPages.logs.disconnected") };
  }
}

export interface AppJournalProps {
  domain: string;
  /** What the query asked, read by the tab: a followed stream is filtered here, line by line. */
  filter: QueryNode | null;
  /** The command that reads the same from a terminal, once. */
  command: string;
}

/**
 * The app's journal as systemd writes it: a backlog, then every new line as it arrives. The
 * stream reconnects by itself; the viewer follows the newest line until the operator scrolls up,
 * the toolbar's query keeps the lines that match it, and the viewer's level chips narrow what is
 * left to what looks like a problem. The viewer is as tall as the screen leaves it, so the page
 * itself barely scrolls.
 */
export function AppJournal({ domain, filter, command }: AppJournalProps) {
  const t = useT();
  const stream = useLogStream(domain, { lines: BACKLOG, cap: LOG_CAP });
  const read = useMemo(() => stream.lines.map(withLevel), [stream.lines]);
  const lines = useMemo(
    () => (filter === null ? read : read.filter((line) => keeps(filter, { message: line.text, priority: priorityOfViewerLevel(line.level), app: domain }))),
    [read, filter, domain],
  );
  const connection = connectionView(t, stream.status);

  return (
    <div className="flex flex-col gap-3">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-1">
        <StatusPill state={connection.state} label={connection.label} size="sm" />
        <span className="text-12 text-fg-muted tabular-nums">
          {stream.status === "connecting" && read.length === 0
            ? t("appPages.logs.readingBacklog", { count: BACKLOG })
            : t("appPages.logs.lineCount", { count: lines.length })}
        </span>
      </div>

      {stream.error !== null ? (
        <ErrorBlock live compact error={{ detail: stream.error }} title={t("appPages.logs.streamFailed")} hint={t("appPages.logs.streamFailedHint")} />
      ) : null}
      {stream.truncated ? (
        <p className="max-w-measure text-12 text-pretty text-fg-muted">
          {t.rich("appPages.logs.truncatedNotice", {
            lines: formatCount(LOG_CAP, t.locale),
            command: <Mono>{`noust logs ${domain} --lines 50000`}</Mono>,
          })}
        </p>
      ) : null}

      <div className="h-editor">
        <LogViewer
          lines={lines}
          height="fill"
          searchable={false}
          loading={stream.status === "connecting"}
          label={t("appPages.logs.journalLabel", { domain })}
          filename={`${domain}-journal.log`}
          emptyMessage={stream.status === "open" ? t("appPages.logs.emptyOpen") : t("appPages.logs.emptyConnecting")}
        />
      </div>
      <CommandHint command={command} label={t("common.words.fromTerminal")} />
    </div>
  );
}
