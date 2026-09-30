import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { LogViewer } from "../../components/ui/LogViewer";
import { StatusGlyph, stateTextClass } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { useLogStream } from "../../realtime/sockets";

/** The unit's journal as it is written, followed while the tab is open, the screen's height. */
export function ServiceLogsTab({ name }: { name: string }) {
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
        <LogViewer lines={stream.lines} label={t("services.detail.logsLabel", { name })} filename={`${name}.log`} height="fill" pageSearch />
      </div>
      {stream.truncated ? <p className="text-12 text-fg-faint">{t("services.detail.truncated")}</p> : null}
      <CommandHint command={`noust server logs ${name} -f`} label={t("services.fromTerminal")} />
    </div>
  );
}
