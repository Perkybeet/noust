/**
 * Access and sessions: who has entered this machine, by SSH or through the console's terminal, and what the terminal
 * recorded. The sessions and the recordings of the terminal had an API and no page: a person who was asked "who was in
 * the server on Tuesday?" had to read the audit log and a file of the server. SSH is read from the server's journal,
 * the terminal from its own recordings (which take `audit.read`); a part a role may not read is left out and said.
 */

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { RelativeTime } from "../../../components/page/RelativeTime";
import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { useCan } from "../../../components/page/permissions";
import { Button } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { Drawer } from "../../../components/ui/Drawer";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { SkeletonCard, SkeletonText } from "../../../components/ui/Skeleton";
import { StatusPill } from "../../../components/ui/StatusPill";
import { SystemOutput } from "../../../components/ui/SystemOutput";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatDuration } from "../../../lib/format";
import { terminalRecordingQuery, terminalRecordingsQuery } from "../../terminal/queries";
import { ServerErrorBlock, ServerQueryState } from "../errors";
import { sshQuery } from "../queries";
import { accessRows } from "./accessData";
import type { AccessRow, RecordingView } from "./accessData";

function wayWords(t: T, row: AccessRow): string {
  if (row.way === "console") return t("server.access.byConsole");
  return row.method === null || row.method === "" ? t("server.access.bySsh") : t("server.access.bySshMethod", { method: row.method });
}

/** What the terminal recorded of a session, as it was printed and with whether the recording is intact. */
function RecordingDrawer({ id, onClose }: { id: string; onClose: () => void }) {
  const t = useT();
  const recording = useQuery(terminalRecordingQuery(id));
  const read: RecordingView | undefined = recording.data;
  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="lg"
      title={t("server.access.recordingTitle")}
      description={read?.recording.started_at != null ? <RelativeTime value={read.recording.started_at} /> : undefined}
    >
      {recording.isError ? (
        <ServerErrorBlock compact error={recording.error} title={t("server.access.recordingFailed")} onRetry={() => void recording.refetch()} />
      ) : read === undefined ? (
        <SkeletonText lines={6} />
      ) : (
        <div className="flex min-w-0 flex-col gap-3">
          <p className="flex flex-wrap gap-x-3 text-13 text-fg-muted">
            {read.recording.actor != null ? <span>{read.recording.actor}</span> : null}
            {read.recording.duration != null ? <span>{formatDuration(Math.round(read.recording.duration), t.locale)}</span> : null}
          </p>
          {read.intact === false ? <StatusPill state="failed" label={t("server.access.notIntact")} size="sm" /> : null}
          {/* What the terminal printed, as it printed it. */}
          <SystemOutput label={t("server.access.transcriptLabel")} maxHeight="max-h-96">
            {read.transcript !== "" ? read.transcript : t("server.access.emptyTranscript")}
          </SystemOutput>
        </div>
      )}
    </Drawer>
  );
}

function Entries({ rows, onOpen }: { rows: readonly AccessRow[]; onOpen: (id: string) => void }) {
  const t = useT();
  const columns: Column<AccessRow>[] = [
    { id: "who", header: t("server.access.who"), card: "title", sortValue: (row) => row.who, cell: (row) => <Mono>{row.who}</Mono> },
    { id: "by", header: t("server.access.by"), hideBelow: "sm", cell: (row) => <span className="text-13 text-fg">{wayWords(t, row)}</span> },
    { id: "from", header: t("server.access.from"), hideBelow: "md", cell: (row) => (row.from !== null ? <Mono tone="muted">{row.from}</Mono> : <EmptyCell />) },
    {
      id: "when",
      header: t("server.access.when"),
      card: "meta",
      sortValue: (row) => (row.live ? Number.MAX_SAFE_INTEGER : (row.at ?? 0)),
      cell: (row) => (row.live ? <StatusPill state="running" label={t("server.access.now")} appearance="inline" size="sm" /> : <RelativeTime value={row.at} className="text-13 text-fg-muted" />),
    },
  ];
  return (
    <DataTable
      embedded
      columns={columns}
      rows={rows}
      getRowId={(row) => row.key}
      caption={t("server.access.caption")}
      density="compact"
      pagination="access"
      defaultSort={{ column: "when", direction: "descending" }}
      rowActions={(row) =>
        row.recording !== null ? (
          <Button size="sm" variant="ghost" aria-label={t("server.access.watchNamed", { who: row.who })} onClick={() => onOpen(row.recording ?? "")}>
            {t("server.access.watch")}
          </Button>
        ) : null
      }
      empty={<EmptyState variant="inline" title={t("server.access.none")} />}
    />
  );
}

function Content() {
  const t = useT();
  const canWatch = useCan("audit.read");
  const ssh = useQuery(sshQuery());
  const recordings = useQuery({ ...terminalRecordingsQuery(), enabled: canWatch, retry: false });
  const [open, setOpen] = useState<string | null>(null);
  return (
    <ServerQueryState query={ssh} label={t("server.ssh.label")} skeleton={<SkeletonCard lines={5} />}>
      {(data) => {
        const rows = accessRows(data, recordings.data?.recordings);
        return (
          <div className="flex min-w-0 flex-col gap-4">
            <SurfaceSection title={t("server.access.title")} icon={OBJECT_ICONS.session} description={data.logins.error !== "" ? data.logins.error : undefined} flush>
              <Entries rows={rows} onOpen={setOpen} />
            </SurfaceSection>
            {!canWatch ? <p className="text-13 text-fg-muted">{t("server.access.noRecordings")}</p> : null}
            {recordings.isError ? <ServerErrorBlock compact error={recordings.error} title={t("server.access.recordingsFailed")} /> : null}
            {open !== null ? <RecordingDrawer id={open} onClose={() => setOpen(null)} /> : null}
          </div>
        );
      }}
    </ServerQueryState>
  );
}

/** The Access and sessions section. */
export function AccessView() {
  return <Content />;
}
