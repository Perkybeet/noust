import { useInfiniteQuery, useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { request } from "../../../api/client";
import { databaseKeys, keyQuery } from "../../../api/queries/databases";
import type { KeyValue, KeysPage, RedisKey } from "../../../api/queries/databases";
import { CommandHint } from "../../../components/page/CommandHint";
import { FilterBar } from "../../../components/page/FilterBar";
import { KeyValueList } from "../../../components/page/KeyValueList";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { DataTable } from "../../../components/ui/DataTable";
import { Drawer } from "../../../components/ui/Drawer";
import { EmptyCell } from "../../../components/ui/EmptyCell";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { Notice } from "../../../components/ui/Notice";
import { Select } from "../../../components/ui/Select";
import { Skeleton } from "../../../components/ui/Skeleton";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes, formatCount, formatDuration } from "../../../lib/format";
import { Cell } from "../grid/Cell";
import type { DataSearch, DataSearchPatch } from "./search";

const TYPES = ["string", "hash", "list", "set", "zset", "stream"] as const;
const ALL = "all";
const SCAN_COUNT = 200;

function ttlWords(ttl: number | null | undefined, t: T): string {
  return ttl == null ? t("databases.keys.noExpiry") : formatDuration(ttl, t.locale);
}

/** A key's value, bounded, drawn by its type: text, elements, fields, members with scores. */
function ValueView({ value, t }: { value: KeyValue; t: T }) {
  const data: unknown = value.value;
  if (data === null || data === undefined) return <p className="text-13 text-fg-muted">{t("databases.keys.noPreview")}</p>;
  if (Array.isArray(data)) {
    const pairs = value.type === "hash" || value.type === "zset";
    return (
      <ol className="flex flex-col divide-y divide-border rounded-card border border-border">
        {(data as unknown[]).map((item, index) => {
          const pair = pairs && Array.isArray(item) ? (item as unknown[]) : null;
          return (
            <li key={index} className="flex min-w-0 items-start gap-3 px-3 py-1.5 text-12">
              <span className="w-8 shrink-0 text-right text-fg-faint tabular-nums">{formatCount(index + 1, t.locale)}</span>
              {pair !== null ? (
                <>
                  <span className="min-w-0 flex-1">
                    <Cell value={pair[0]} inline={false} />
                  </span>
                  <span className="min-w-0 flex-1">
                    <Cell value={pair[1]} inline={false} />
                  </span>
                </>
              ) : (
                <span className="min-w-0 flex-1">
                  <Cell value={item} inline={false} />
                </span>
              )}
            </li>
          );
        })}
      </ol>
    );
  }
  return (
    <div className="rounded-card border border-border bg-bg-sunken px-3 py-2 text-13">
      <Cell value={data} inline={false} />
    </div>
  );
}

function KeyDrawer({ engine, name, redisKey, onClose }: { engine: string; name: string; redisKey: RedisKey; onClose: () => void }) {
  const t = useT();
  const value = useQuery(keyQuery(engine, name, redisKey));
  return (
    <Drawer open onOpenChange={(open) => (open ? undefined : onClose())} size="md" title={<Mono tone="default">{redisKey.key}</Mono>} description={t("databases.keys.previewDescription")}>
      {value.isError ? (
        <ErrorBlock compact error={value.error} title={t("databases.keys.previewFailed")} onRetry={() => void value.refetch()} />
      ) : value.data === undefined ? (
        <div aria-busy="true" className="flex flex-col gap-2">
          <span className="sr-only">{t("databases.keys.previewLoading")}</span>
          <Skeleton className="h-24 w-full" />
        </div>
      ) : (
        <div className="flex flex-col gap-4">
          <KeyValueList
            items={[
              { label: t("databases.keys.type"), value: value.data.type },
              { label: t("databases.keys.ttl"), value: ttlWords(value.data.ttl, t), mono: false, copy: false },
              ...(value.data.memory != null ? [{ label: t("databases.keys.memory"), value: formatBytes(value.data.memory, t.locale), copy: false as const }] : []),
              ...(value.data.length != null ? [{ label: t("databases.keys.length"), value: formatCount(value.data.length, t.locale), copy: false as const }] : []),
            ]}
          />
          {value.data.truncated ? <Notice title={t("databases.keys.truncated")}>{t("databases.keys.truncatedBody")}</Notice> : null}
          <ValueView value={value.data} t={t} />
        </div>
      )}
    </Drawer>
  );
}

/**
 * A Redis slot's keys: a scan matched by a glob and narrowed by type, a page at a time with
 * "Load more", each key's type, expiry and memory, and its value in a drawer. Read as the
 * read-only ACL user where the server has ACLs, which says so when it does not.
 */
export function KeysView({ engine, name, search, onSearchChange }: { engine: string; name: string; search: DataSearch; onSearchChange: (patch: DataSearchPatch) => void }) {
  const t = useT();
  const [open, setOpen] = useState<RedisKey | null>(null);
  // One scan, a page at a time: each page's cursor is where the next starts; "0" ends it.
  const scan = useInfiniteQuery({
    queryKey: [...databaseKeys.database(engine, name), "scan", { match: search.match ?? null, type: search.type ?? null }],
    initialPageParam: "0",
    queryFn: ({ pageParam, signal }) =>
      request("get", "/api/databases/databases/{engine}/{name}/keys", {
        params: { engine, name },
        query: {
          cursor: pageParam,
          count: SCAN_COUNT,
          ...(search.match !== undefined ? { match: search.match } : {}),
          ...(search.type !== undefined ? { type: search.type } : {}),
        },
        signal,
      }),
    getNextPageParam: (last: KeysPage) => (last.done ? undefined : last.cursor),
  });
  const shown = (scan.data?.pages ?? []).flatMap((item) => item.keys);
  const last = scan.data?.pages.at(-1);
  const done = !scan.hasNextPage;

  return (
    <div className="flex min-w-0 flex-col gap-4">
      {last !== undefined && !last.read_only_enforced ? <Notice title={t("databases.keys.notEnforced")}>{t("databases.keys.notEnforcedBody")}</Notice> : null}
      <FilterBar
        label={t("databases.keys.filterLabel")}
        search={{
          value: search.match ?? "",
          onChange: (value) => onSearchChange({ match: value === "" ? undefined : value }),
          label: t("databases.keys.searchLabel"),
          placeholder: t("databases.keys.searchPlaceholder"),
        }}
        filters={
          <Select
            aria-label={t("databases.keys.typeLabel")}
            value={search.type ?? ALL}
            onValueChange={(value) => onSearchChange({ type: value === ALL ? undefined : value })}
            options={[{ value: ALL, label: t("databases.keys.everyType") }, ...TYPES.map((type) => ({ value: type, label: type }))]}
            className="min-w-36"
          />
        }
        count={shown.length > 0 ? (done ? t("databases.keys.count", { count: shown.length }) : t("databases.keys.countSoFar", { count: shown.length })) : ""}
      />
      {scan.isError && shown.length === 0 ? (
        <ErrorBlock error={scan.error} title={t("databases.keys.scanFailed")} onRetry={() => void scan.refetch()} retrying={scan.isRefetching} />
      ) : (
        <DataTable
          caption={t("databases.keys.caption", { name })}
          density="compact"
          rows={shown}
          getRowId={(key) => key.hex ?? key.key}
          loading={scan.isPending}
          skeletonRows={8}
          onRowActivate={setOpen}
          mobile="cards"
          empty={<EmptyState variant="inline" title={search.match !== undefined || search.type !== undefined ? t("databases.keys.noMatch") : t("databases.keys.empty")} />}
          columns={[
            { id: "key", header: t("databases.keys.key"), card: "title", cell: (key) => <Mono truncate>{key.key}</Mono> },
            { id: "type", header: t("databases.keys.type"), width: "w-28", cell: (key) => <Mono tone="muted">{key.type}</Mono> },
            { id: "ttl", header: t("databases.keys.ttl"), width: "w-36", cell: (key) => ttlWords(key.ttl, t) },
            {
              id: "memory",
              header: t("databases.keys.memory"),
              align: "end",
              mono: true,
              width: "w-28",
              cell: (key) => (key.memory != null ? formatBytes(key.memory, t.locale) : <EmptyCell reason={t("databases.keys.noMemory")} />),
            },
          ]}
        />
      )}
      {!done && shown.length > 0 ? (
        <div>
          <Button size="sm" loading={scan.isFetchingNextPage} onClick={() => void scan.fetchNextPage()}>
            {t("databases.keys.loadMore")}
          </Button>
        </div>
      ) : null}
      <CommandHint command={`noust db keys ${name}${search.match ? ` --match '${search.match}'` : ""}`} label={t("databases.common.fromTerminal")} />
      {open !== null ? <KeyDrawer engine={engine} name={name} redisKey={open} onClose={() => setOpen(null)} /> : null}
    </div>
  );
}
