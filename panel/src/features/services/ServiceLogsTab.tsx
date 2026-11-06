import { Link } from "@tanstack/react-router";

import { CommandHint } from "../../components/page/CommandHint";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { LogViewer } from "../../components/ui/LogViewer";
import { Skeleton } from "../../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { useLogStream } from "../../realtime/sockets";
import { useServiceRecord } from "./useServiceRecord";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/** The unit's journal as it is written, followed while the tab is open, the screen's height. */
function ManagedLogs({ name }: { name: string }) {
  const t = useT();
  const stream = useLogStream(name);
  const state = stream.status === "open" ? "running" : stream.status === "reconnecting" ? "warning" : "queued";
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <div className="flex min-w-0 flex-wrap items-center justify-between gap-3">
        <p className="text-14 text-fg-muted">{t("services.detail.logsDescription")}</p>
        <span role="status" className="flex items-center gap-1.5 text-12 text-fg-muted">
          <StatusGlyph state={state} size={10} className={stateTextClass(state)} />
          {stream.status === "open" ? t("services.detail.live") : stream.status === "reconnecting" ? t("services.detail.reconnecting") : t("services.detail.connecting")}
        </span>
      </div>
      {stream.error !== null ? <ErrorBlock compact error={{ detail: stream.error }} title={t("services.detail.logStreamFailed")} /> : null}
      <div className="flex h-editor min-h-0 min-w-0 flex-col">
        <LogViewer
          lines={stream.lines}
          label={t("services.detail.logsLabel", { name })}
          filename={`${name}.log`}
          height="fill"
          pageSearch
          loading={stream.status === "connecting"}
          {...(stream.status === "connecting" ? { emptyMessage: t("services.detail.connecting") } : {})}
        />
      </div>
      {stream.truncated ? <p className="text-12 text-fg-faint">{t("services.detail.truncated")}</p> : null}
      <CommandHint command={`noust server logs ${name} -f`} label={t("common.words.fromTerminal")} />
    </div>
  );
}

/**
 * The server does not follow the journal of a unit Noust did not create, and the column does not offer this section for
 * one; a person who arrives by an address is told so, and shown where the system's own journal is.
 */
function ForeignLogs({ name }: { name: string }) {
  const t = useT();
  return (
    <EmptyState
      variant="inline"
      icon={<OBJECT_ICONS.log />}
      title={t("services.detail.foreignLogsTitle")}
      description={t("services.detail.foreignLogsDescription")}
      action={
        <Link to="/server/logs" search={{ unit: name }} className={buttonClassName("secondary", "sm")}>
          {t("services.actions.openInLogs")}
        </Link>
      }
    />
  );
}

/** A service's Logs section: the journal followed live, or, for a unit that is not Noust's, where its journal is. */
export function ServiceLogsTab({ name }: { name: string }) {
  const t = useT();
  const record = useServiceRecord(name);
  // The stream is not opened until it is known whose the unit is: a refusal to follow a foreign unit is a socket that failed.
  if (record.loading) {
    return (
      <LoadingRegion label={t("services.detail.connecting")}>
        <Skeleton className="h-editor w-full rounded-card" />
      </LoadingRegion>
    );
  }
  return record.foreign ? <ForeignLogs name={name} /> : <ManagedLogs name={name} />;
}
