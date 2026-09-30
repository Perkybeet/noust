import { Play } from "lucide-react";

import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { StatusGlyph, StatusPill, stateTextClass } from "../../components/ui/StatusPill";
import type { Status } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { PlainKey, T } from "../../i18n";
import { cx } from "../../lib/cx";
import { useFleetActions } from "./BulkActionDialog";
import { noustOf, originOf, osUpdatesOf } from "./data";
import type { FleetRow, NodeOutcome, NoustUpdateState } from "./data";
import { isFiltered, matchesQuery, patchSearch } from "./filters";
import type { FleetSearch, FleetSearchPatch } from "./filters";
import { PartialNotice, ServerCell, StateFilter, useFleetView } from "./parts";

const NOUST_VIEW: Readonly<Record<NoustUpdateState, { state: Status; label: PlainKey }>> = {
  up_to_date: { state: "running", label: "fleet.updates.noust.upToDate" },
  update_available: { state: "warning", label: "fleet.updates.noust.available" },
  on_the_way: { state: "queued", label: "fleet.updates.noust.onTheWay" },
  unknown: { state: "unknown", label: "fleet.updates.noust.unknown" },
};

const UPDATE_STATES = ["noust", "os", "reboot"] as const;

function needs(row: FleetRow, what: (typeof UPDATE_STATES)[number]): boolean {
  if (what === "noust") return noustOf(row).state === "update_available";
  const os = osUpdatesOf(row);
  if (what === "os") return (os?.pending ?? 0) > 0;
  return os?.reboot === true;
}

function columns(t: T, outcomes: ReadonlyMap<string, NodeOutcome>): Column<FleetRow>[] {
  return [
    {
      id: "server",
      header: t("fleet.column.server"),
      card: "title",
      sortValue: (row) => (originOf(row).local ? "" : originOf(row).node),
      cell: (row) => <ServerCell origin={originOf(row)} outcome={outcomes.get(originOf(row).node)} />,
    },
    {
      id: "noust",
      header: t("fleet.column.noust"),
      card: "status",
      cell: (row) => {
        const noust = noustOf(row);
        const view = NOUST_VIEW[noust.state];
        return (
          <span className="flex min-w-0 flex-col items-start gap-0.5">
            <StatusPill state={view.state} label={t(view.label)} appearance="inline" size="sm" />
            {noust.current !== null ? (
              <Mono tone="muted" className="text-12">
                {noust.state === "update_available" && noust.latest !== null
                  ? t("fleet.updates.noust.fromTo", { current: noust.current, latest: noust.latest })
                  : noust.current}
              </Mono>
            ) : null}
          </span>
        );
      },
    },
    {
      id: "method",
      header: t("fleet.column.method"),
      hideBelow: "lg",
      card: "meta",
      cell: (row) => {
        const method = noustOf(row).method;
        return method === null ? <EmptyCell reason={t("fleet.updates.noMethod")} /> : <Mono>{method}</Mono>;
      },
    },
    {
      id: "os",
      header: t("fleet.column.osUpdates"),
      card: "meta",
      cell: (row) => {
        const os = osUpdatesOf(row);
        const pending = os?.pending ?? null;
        if (os === null || pending === null) return <EmptyCell reason={t("fleet.updates.osUnknown")} />;
        if (pending === 0) return <span className="text-13 text-fg-muted">{t("fleet.updates.osNone")}</span>;
        return (
          <span className="flex flex-col text-13">
            <span className="text-fg tabular-nums">{t("fleet.updates.osPending", { count: pending })}</span>
            {os.security !== null && os.security > 0 ? (
              <span className={cx("inline-flex items-center gap-1 font-medium", stateTextClass("warning"))}>
                <StatusGlyph state="warning" size={10} />
                {t("fleet.updates.osSecurity", { count: os.security })}
              </span>
            ) : null}
          </span>
        );
      },
    },
    {
      id: "reboot",
      header: t("fleet.column.reboot"),
      hideBelow: "md",
      card: "meta",
      cell: (row) => {
        const reboot = osUpdatesOf(row)?.reboot ?? null;
        if (reboot === null) return <EmptyCell reason={t("fleet.updates.osUnknown")} />;
        return reboot ? (
          <span className={cx("inline-flex items-center gap-1 text-13 font-medium", stateTextClass("warning"))}>
            <StatusGlyph state="warning" size={10} />
            {t("fleet.updates.rebootNeeded")}
          </span>
        ) : (
          <span className="text-13 text-fg-muted">{t("fleet.updates.rebootNo")}</span>
        );
      },
    },
  ];
}

export interface UpdatesTabProps {
  search: FleetSearch;
  onSearchChange: (search: FleetSearch, options?: { replace?: boolean }) => void;
}

/**
 * Every server's Noust and operating system updates (T1): which Noust each runs and whether a
 * newer one is out, how it updates, what the system has pending and whether it waits for a
 * restart. Updating runs as a bulk action, a server at a time by default; this central updates
 * last, on its own, so it can watch every server come back.
 */
export function UpdatesTab({ search, onSearchChange }: UpdatesTabProps) {
  const t = useT();
  const view = useFleetView("updates");
  const actions = useFleetActions();
  const rows = view.data?.items ?? [];
  const shown = rows.filter((row) => {
    if (search.state !== undefined && !UPDATE_STATES.some((what) => what === search.state && needs(row, what))) return false;
    return matchesQuery([originOf(row).node, noustOf(row).current], search.q);
  });
  const central = rows.find((row) => originOf(row).local);
  const centralNoust = central === undefined ? null : noustOf(central);
  const nodesFor = (what: (typeof UPDATE_STATES)[number]): string[] => rows.filter((row) => !originOf(row).local && needs(row, what)).map((row) => originOf(row).node);

  const set = (patch: FleetSearchPatch, replace = false): void => {
    onSearchChange(patchSearch(search, patch), { replace });
  };

  if (view.isError && view.data === undefined) {
    return <ErrorBlock error={view.error} title={t("fleet.page.loadFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <PartialNotice view={view.data} />
      {centralNoust !== null && centralNoust.state === "update_available" && centralNoust.command !== null ? (
        <Notice title={t("fleet.updates.central.title", { version: centralNoust.latest ?? "" })}>
          <div className="flex flex-col gap-2">
            <p className="max-w-measure text-pretty">{t("fleet.updates.central.body")}</p>
            <CommandHint label={t("fleet.updates.central.command")} command={centralNoust.command} />
          </div>
        </Notice>
      ) : null}
      <FilterBar
        label={t("fleet.updates.filterLabel")}
        search={{
          value: search.q ?? "",
          onChange: (value) => {
            set({ q: value }, true);
          },
          label: t("fleet.updates.searchLabel"),
          placeholder: t("fleet.updates.searchPlaceholder"),
        }}
        filters={
          <StateFilter
            value={search.state}
            onChange={(state) => {
              set({ state });
            }}
            options={UPDATE_STATES.map((state) => ({ value: state, label: t(`fleet.updates.state.${state}`) }))}
          />
        }
        count={view.data === undefined ? "" : isFiltered(search) ? t("fleet.updates.countFiltered", { shown: shown.length, total: rows.length }) : t("fleet.updates.count", { count: rows.length })}
        actions={
          <>
            <Button
              onClick={() => {
                actions.open({ action: "os_updates", nodes: nodesFor("os") });
              }}
            >
              {t("fleet.updates.applyOs")}
            </Button>
            <Button
              icon={<Play aria-hidden="true" />}
              onClick={() => {
                actions.open({ action: "noust_update", nodes: nodesFor("noust") });
              }}
            >
              {t("fleet.updates.updateNoust")}
            </Button>
          </>
        }
      />
      <DataTable
        caption={t("fleet.updates.caption")}
        columns={columns(t, view.outcomes)}
        rows={shown}
        getRowId={(row) => originOf(row).node}
        loading={view.isPending}
        skeletonRows={3}
        mobile="cards"
        empty={
          <EmptyState
            variant="inline"
            title={t("fleet.filters.noMatch")}
            action={
              <Button
                size="sm"
                variant="ghost"
                onClick={() => {
                  onSearchChange({});
                }}
              >
                {t("fleet.filters.clear")}
              </Button>
            }
          />
        }
      />
      <CommandHint label={t("fleet.page.fromTerminal")} command="noust fleet updates" />
    </div>
  );
}
