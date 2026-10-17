/**
 * What was logged around one entry (checklist M-40, "view in context"): the entries before it,
 * itself marked, and the entries after it, from every unit or from its own. The reading is the
 * server's (`/api/server/logs/context`, `journalctl --cursor`), so the order and the neighbours are
 * the journal's, not a guess from the lines on screen.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { Drawer } from "../../../components/ui/Drawer";
import { LogViewer } from "../../../components/ui/LogViewer";
import { Notice } from "../../../components/ui/Notice";
import { Switch } from "../../../components/ui/Switch";
import { useT } from "../../../i18n";
import { ServerErrorBlock } from "../errors";
import { journalContextQuery } from "../queries";
import { journalLines } from "./data";

/** Entries read on each side of the one opened. */
const AROUND = 25;

export interface ContextEntry {
  cursor: string;
  unit: string;
  timestamp: string;
}

export interface ContextDrawerProps {
  entry: ContextEntry;
  onClose: () => void;
}

export function ContextDrawer({ entry, onClose }: ContextDrawerProps) {
  const t = useT();
  const [onlyUnit, setOnlyUnit] = useState(false);
  const unit = onlyUnit && entry.unit !== "" ? entry.unit : null;
  const context = useQuery(journalContextQuery(entry.cursor, AROUND, AROUND, unit));
  const lines = useMemo(() => (context.data ? journalLines(context.data) : []), [context.data]);
  const target = context.data?.target ?? null;

  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="lg"
      title={t("server.logs.context.title")}
      description={t("server.logs.context.description", { time: entry.timestamp.replace(/\.\d+/, "").replace("T", " ") })}
    >
      <div className="flex min-w-0 flex-col gap-3">
        {entry.unit !== "" ? (
          <Switch label={t("server.logs.context.onlyUnit", { unit: entry.unit })} checked={onlyUnit} onCheckedChange={setOnlyUnit} />
        ) : null}
        {context.isError ? (
          <ServerErrorBlock compact error={context.error} title={t("server.logs.context.loadFailed")} onRetry={() => void context.refetch()} retrying={context.isRefetching} />
        ) : null}
        {context.data !== undefined && target === null ? <Notice tone="warning">{t("server.logs.context.gone")}</Notice> : null}
        <div className="flex h-editor min-h-0 min-w-0 flex-col">
          <LogViewer
            lines={lines}
            follow={false}
            height="fill"
            searchable={false}
            loading={context.isPending}
            {...(target !== null ? { highlightId: target } : {})}
            label={t("server.logs.context.viewerLabel")}
            filename="journal-context.log"
            emptyMessage={context.isPending ? t("server.logs.loading") : t("server.logs.empty")}
          />
        </div>
      </div>
    </Drawer>
  );
}
