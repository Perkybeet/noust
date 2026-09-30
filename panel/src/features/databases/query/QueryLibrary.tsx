import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Trash2 } from "lucide-react";
import { useState } from "react";

import { request } from "../../../api/client";
import { databaseKeys, historyQuery, savedQueriesQuery } from "../../../api/queries/databases";
import type { HistoryEntry, SavedQuery } from "../../../api/queries/databases";
import { ErrorBlock } from "../../../components/page/QueryState";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { Button } from "../../../components/ui/Button";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { IconButton } from "../../../components/ui/IconButton";
import { Mono } from "../../../components/ui/Mono";
import { Skeleton } from "../../../components/ui/Skeleton";
import { StatusGlyph, stateTextClass } from "../../../components/ui/StatusPill";
import { Tab, TabList, TabPanel, Tabs } from "../../../components/ui/Tabs";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatCount, formatDecimal } from "../../../lib/format";
import { reportActionError } from "../../apps/useAppActions";

function ListSkeleton() {
  return (
    <div aria-hidden="true" className="flex flex-col gap-2 pt-2">
      {[0, 1, 2, 3].map((index) => (
        <Skeleton key={index} className="h-12 w-full" />
      ))}
    </div>
  );
}

function HistoryItem({ entry, onPick, t }: { entry: HistoryEntry; onPick: (statement: string) => void; t: T }) {
  const failed = entry.outcome !== "ok";
  return (
    <li>
      <button
        type="button"
        onClick={() => onPick(entry.statement)}
        className="flex w-full min-w-0 cursor-pointer flex-col gap-1 rounded-control px-2 py-2 text-left hover:bg-surface-hover"
      >
        <Mono tone="default" className="line-clamp-2 break-all whitespace-pre-wrap">
          {entry.statement}
        </Mono>
        <span className="flex min-w-0 flex-wrap items-center gap-x-2 text-12 text-fg-muted">
          <span className="inline-flex items-center gap-1">
            <StatusGlyph state={failed ? "failed" : "running"} size={10} className={stateTextClass(failed ? "failed" : "running")} />
            {failed ? t("databases.library.failed") : entry.mode === "write" ? t("databases.library.wrote") : t("databases.library.read")}
          </span>
          {entry.row_count != null ? <span>{t("databases.library.rows", { count: entry.row_count, rows: formatCount(entry.row_count, t.locale) })}</span> : null}
          {entry.duration_ms != null ? <span>{t("databases.library.ms", { ms: formatDecimal(entry.duration_ms, t.locale) })}</span> : null}
          <RelativeTime value={entry.created_at} className="text-fg-faint" />
        </span>
      </button>
    </li>
  );
}

function SavedItem({ saved, onPick, onDelete, t }: { saved: SavedQuery; onPick: (statement: string) => void; onDelete: () => void; t: T }) {
  return (
    <li className="flex min-w-0 items-start gap-1 rounded-control hover:bg-surface-hover">
      <button type="button" onClick={() => onPick(saved.statement)} className="flex min-w-0 flex-1 cursor-pointer flex-col gap-1 px-2 py-2 text-left">
        <span className="truncate text-13 font-medium text-fg">{saved.name}</span>
        <Mono tone="muted" className="line-clamp-2 break-all whitespace-pre-wrap">
          {saved.statement}
        </Mono>
        {saved.database === "" ? <span className="text-12 text-fg-faint">{t("databases.library.anyDatabase")}</span> : null}
      </button>
      <IconButton size="sm" label={t("databases.library.deleteSaved", { name: saved.name })} icon={<Trash2 />} onClick={onDelete} className="mt-1.5" />
    </li>
  );
}

/**
 * The statements the operator ran here and the ones they kept under a name, newest first: a
 * click puts one back in the editor. Each operator's own; the text is stored with quoted
 * secrets replaced.
 */
export function QueryLibrary({ engine, name, onPick }: { engine: string; name: string; onPick: (statement: string) => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const history = useQuery(historyQuery(engine, name));
  const saved = useQuery(savedQueriesQuery(engine, name));
  const [clearing, setClearing] = useState(false);

  const remove = useMutation({
    mutationFn: (id: number) => request("delete", "/api/databases/console/saved/{saved_id}", { params: { saved_id: id } }),
    onSuccess: () => void queryClient.invalidateQueries({ queryKey: databaseKeys.saved(engine, name) }),
    onError: (error) => reportActionError(t("databases.library.deleteFailed"), error),
  });

  return (
    <Tabs defaultValue="history" className="min-w-0 gap-2">
      <TabList aria-label={t("databases.library.label")}>
        <Tab value="history" count={history.data?.entries.length ?? null}>
          {t("databases.library.history")}
        </Tab>
        <Tab value="saved" count={saved.data?.queries.length ?? null}>
          {t("databases.library.saved")}
        </Tab>
      </TabList>
      <TabPanel value="history">
        {history.isError ? (
          <ErrorBlock compact error={history.error} title={t("databases.library.historyFailed")} onRetry={() => void history.refetch()} />
        ) : history.data === undefined ? (
          <ListSkeleton />
        ) : history.data.entries.length === 0 ? (
          <EmptyState variant="inline" title={t("databases.library.noHistory")} />
        ) : (
          <div className="flex flex-col gap-2">
            <ul className="flex max-h-editor flex-col overflow-y-auto scroll-thin">
              {history.data.entries.map((entry) => (
                <HistoryItem key={entry.id} entry={entry} onPick={onPick} t={t} />
              ))}
            </ul>
            <div>
              <Button size="sm" variant="ghost" onClick={() => setClearing(true)}>
                {t("databases.library.clear")}
              </Button>
            </div>
          </div>
        )}
      </TabPanel>
      <TabPanel value="saved">
        {saved.isError ? (
          <ErrorBlock compact error={saved.error} title={t("databases.library.savedFailed")} onRetry={() => void saved.refetch()} />
        ) : saved.data === undefined ? (
          <ListSkeleton />
        ) : saved.data.queries.length === 0 ? (
          <EmptyState variant="inline" title={t("databases.library.noSaved")} />
        ) : (
          <ul className="flex max-h-editor flex-col overflow-y-auto scroll-thin">
            {saved.data.queries.map((item) => (
              <SavedItem key={item.id} saved={item} onPick={onPick} onDelete={() => remove.mutate(item.id)} t={t} />
            ))}
          </ul>
        )}
      </TabPanel>
      <ConfirmDialog
        friction="simple"
        open={clearing}
        onOpenChange={setClearing}
        title={t("databases.library.clearTitle")}
        description={t("databases.library.clearDescription", { name })}
        actionLabel={t("databases.library.clearAction")}
        onConfirm={async () => {
          await request("delete", "/api/databases/console/history", { query: { engine, database: name } });
          await queryClient.invalidateQueries({ queryKey: databaseKeys.history(engine, name) });
        }}
      />
    </Tabs>
  );
}
