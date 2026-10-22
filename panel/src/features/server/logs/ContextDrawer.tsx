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
import { Checkbox } from "../../../components/ui/Checkbox";
import { useT } from "../../../i18n";
import { ServerErrorBlock } from "../errors";
import { journalContextQuery } from "../queries";
import { journalLines } from "./data";

/** Entries read on each side of the one opened. */
const AROUND = 25;

export interface ContextEntry {
  cursor: string;
  /** Empty when the entry is not known yet (a link to a line names only its cursor). */
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
  const [learned, setLearned] = useState("");
  const known = entry.unit !== "" ? entry.unit : learned;
  const unit = onlyUnit && known !== "" ? known : null;
  const context = useQuery(journalContextQuery(entry.cursor, AROUND, AROUND, unit));
  const lines = useMemo(() => (context.data ? journalLines(context.data) : []), [context.data]);
  const target = context.data?.target ?? null;
  // A link to a line names only the cursor: the moment is read from the entry the server found.
  const found = target !== null ? context.data?.entries[target] : undefined;
  // The unit of a line a link named is the one the server found it in, once it has.
  // Adjusting state while rendering, the way React documents it: it is set once, when the server tells.
  const foundUnit = found?.unit ?? "";
  if (learned === "" && foundUnit !== "") setLearned(foundUnit);
  const stamped = entry.timestamp !== "" ? entry.timestamp : (found?.timestamp ?? "");

  return (
    <Drawer
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="lg"
      title={t("logs.system.context.title")}
      description={
        stamped === ""
          ? t("logs.system.context.descriptionAny")
          : t("logs.system.context.description", { time: stamped.replace(/\.\d+/, "").replace("T", " ") })
      }
    >
      <div className="flex min-w-0 flex-col gap-3">
        {known !== "" ? <Checkbox label={t("logs.system.context.onlyUnit", { unit: known })} checked={onlyUnit} onCheckedChange={setOnlyUnit} /> : null}
        {context.isError ? (
          <ServerErrorBlock compact error={context.error} title={t("logs.system.context.loadFailed")} onRetry={() => void context.refetch()} retrying={context.isRefetching} />
        ) : null}
        {context.data !== undefined && target === null ? <Notice tone="warning">{t("logs.system.context.gone")}</Notice> : null}
        <div className="flex h-editor min-h-0 min-w-0 flex-col">
          <LogViewer
            lines={lines}
            follow={false}
            height="fill"
            searchable={false}
            loading={context.isPending}
            {...(target !== null ? { highlightId: target } : {})}
            label={t("logs.system.context.viewerLabel")}
            filename="journal-context.log"
            emptyMessage={context.isPending ? t("logs.system.loading") : t("logs.system.empty")}
          />
        </div>
      </div>
    </Drawer>
  );
}
