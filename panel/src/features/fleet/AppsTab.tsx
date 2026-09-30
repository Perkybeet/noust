import { Play } from "lucide-react";
import { useState } from "react";

import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ErrorBlock } from "../../components/page/QueryState";
import { appStatus } from "../../components/page/status";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatCount } from "../../lib/format";
import { DeployMoment } from "../apps/AppsTable";
import { useTypeName } from "../apps/data";
import { useFleetActions } from "./BulkActionDialog";
import { field, numberOf, objectOf, originOf } from "./data";
import type { FleetRow, NodeOutcome } from "./data";
import { isFiltered, matchesQuery, patchSearch } from "./filters";
import type { FleetSearch, FleetSearchPatch } from "./filters";
import { HrefLink } from "./links";
import { PartialNotice, ServerCell, ServerFilter, StateFilter, useFleetView } from "./parts";

const APP_STATES = ["running", "failed", "stopped", "deploying"] as const;

/** Rows shown before "Show more": a page of a long list (docs/DESIGN.md, 6.8). */
export const APPS_PAGE = 25;

// The same name as the local list: the domain in the interface's type, not a system value.
const NAME_LINK = "-mx-1 block max-w-full truncate rounded-chip px-1 py-0.5 font-medium text-fg hover:underline hover:underline-offset-2";

function columns(t: T, outcomes: ReadonlyMap<string, NodeOutcome>, typeName: (type: string | null) => string | null): Column<FleetRow>[] {
  return [
    {
      id: "app",
      header: t("fleet.column.app"),
      card: "title",
      sortValue: (row) => field(row, "domain"),
      cell: (row) => (
        <HrefLink href={originOf(row).href} className={NAME_LINK}>
          {field(row, "domain") ?? ""}
        </HrefLink>
      ),
    },
    {
      id: "state",
      header: t("fleet.column.state"),
      width: "w-32",
      card: "status",
      sortValue: (row) => field(row, "status"),
      cell: (row) => {
        const view = appStatus(field(row, "status"), t.locale);
        return <StatusPill state={view.state} label={view.label} appearance="inline" size="sm" />;
      },
    },
    {
      id: "server",
      header: t("fleet.column.server"),
      card: "meta",
      sortValue: (row) => originOf(row).node,
      cell: (row) => <ServerCell origin={originOf(row)} outcome={outcomes.get(originOf(row).node)} />,
    },
    {
      id: "type",
      header: t("fleet.column.type"),
      width: "w-36",
      hideBelow: "md",
      card: "meta",
      cell: (row) => {
        const type = typeName(field(row, "app_type"));
        return type === null ? <EmptyCell reason={t("fleet.apps.noType")} /> : <span className="text-fg-muted">{type}</span>;
      },
    },
    {
      id: "deploy",
      header: t("fleet.column.lastDeploy"),
      width: "w-40",
      hideBelow: "lg",
      card: "meta",
      // As the local list says it: the deploy's state as its glyph (and in words for a screen
      // reader), and when.
      cell: (row) => {
        const last = objectOf(row["last_deployment"]);
        if (last === null) return <EmptyCell reason={t("fleet.apps.neverDeployed")} />;
        const text = (key: string): string | null => (typeof last[key] === "string" ? last[key] : null);
        return <DeployMoment deploy={{ status: text("status") ?? "", finished_at: text("finished_at"), started_at: text("started_at") }} />;
      },
    },
    {
      id: "port",
      header: t("fleet.column.port"),
      align: "end",
      hideBelow: "md",
      card: "meta",
      mono: true,
      sortValue: (row) => numberOf(row["port"]),
      cell: (row) => {
        const port = numberOf(row["port"]);
        return port === null ? <EmptyCell reason={t("fleet.apps.noPort")} /> : <Mono>{String(port)}</Mono>;
      },
    },
  ];
}

export interface AppsTabProps {
  search: FleetSearch;
  onSearchChange: (search: FleetSearch, options?: { replace?: boolean }) => void;
}

/**
 * Every application of every server (T1), each row naming its server and opening the
 * application on it. "New application" asks on which server first, so any server can be
 * deployed to from here.
 */
export function AppsTab({ search, onSearchChange }: AppsTabProps) {
  const t = useT();
  const view = useFleetView("apps");
  const actions = useFleetActions();
  const typeName = useTypeName();
  const [limit, setLimit] = useState(APPS_PAGE);
  const rows = view.data?.items ?? [];
  const shown = rows.filter((row) => {
    const origin = originOf(row);
    if (search.server !== undefined && origin.node !== search.server) return false;
    if (search.state !== undefined && appStatus(field(row, "status"), t.locale).state !== search.state) return false;
    return matchesQuery([field(row, "domain"), field(row, "name"), field(row, "app_type"), origin.node], search.q);
  });

  const set = (patch: FleetSearchPatch, replace = false): void => {
    setLimit(APPS_PAGE);
    onSearchChange(patchSearch(search, patch), { replace });
  };
  const paged = shown.slice(0, limit);

  if (view.isError && view.data === undefined) {
    return <ErrorBlock error={view.error} title={t("fleet.page.loadFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />;
  }

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <PartialNotice view={view.data} />
      <FilterBar
        label={t("fleet.apps.filterLabel")}
        search={{
          value: search.q ?? "",
          onChange: (value) => {
            set({ q: value }, true);
          },
          label: t("fleet.apps.searchLabel"),
          placeholder: t("fleet.apps.searchPlaceholder"),
        }}
        filters={
          <>
            <ServerFilter
              view={view.data}
              value={search.server}
              onChange={(server) => {
                set({ server });
              }}
            />
            <StateFilter
              value={search.state}
              onChange={(state) => {
                set({ state });
              }}
              options={APP_STATES.map((state) => ({ value: state, label: appStatus(state, t.locale).label }))}
            />
          </>
        }
        count={view.data === undefined ? "" : isFiltered(search) ? t("fleet.apps.countFiltered", { shown: shown.length, total: rows.length }) : t("fleet.apps.count", { count: rows.length })}
        actions={
          <>
            <Button
              icon={<Play aria-hidden="true" />}
              onClick={() => {
                actions.open({ action: "apps_update" });
              }}
            >
              {t("fleet.apps.update")}
            </Button>
          </>
        }
      />
      <DataTable
        caption={t("fleet.apps.caption")}
        columns={columns(t, view.outcomes, typeName)}
        rows={paged}
        getRowId={(row) => `${originOf(row).node}:${field(row, "domain") ?? ""}`}
        loading={view.isPending}
        skeletonRows={6}
        mobile="cards"
        empty={
          rows.length === 0 && view.data !== undefined ? (
            <EmptyState variant="inline" title={t("fleet.apps.empty")} />
          ) : (
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
          )
        }
      />
      {shown.length > paged.length ? (
        <div className="flex flex-wrap items-center justify-center gap-3">
          <span className="text-13 text-fg-muted tabular-nums">
            {t("fleet.apps.shownOf", { shown: formatCount(paged.length, t.locale), total: formatCount(shown.length, t.locale) })}
          </span>
          <Button
            onClick={() => {
              setLimit((current) => current + APPS_PAGE);
            }}
          >
            {t("fleet.apps.showMore", { count: Math.min(APPS_PAGE, shown.length - paged.length) })}
          </Button>
        </div>
      ) : null}
      <CommandHint label={t("fleet.page.fromTerminal")} command="noust fleet apps" />
    </div>
  );
}
