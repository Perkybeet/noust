import { Link } from "@tanstack/react-router";
import { Play } from "lucide-react";

import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { appStatus, deployStatus } from "../../components/page/status";
import { Button, buttonClassName } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { Mono } from "../../components/ui/Mono";
import { StatusGlyph, StatusPill, stateTextClass } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { useFleetActions } from "./BulkActionDialog";
import { field, numberOf, objectOf, originOf } from "./data";
import type { FleetRow, NodeOutcome } from "./data";
import { isFiltered, matchesQuery, patchSearch } from "./filters";
import type { FleetSearch, FleetSearchPatch } from "./filters";
import { HrefLink } from "./links";
import { PartialNotice, ServerCell, ServerFilter, StateFilter, useFleetView } from "./parts";

const APP_STATES = ["running", "failed", "stopped", "deploying"] as const;

const NAME_LINK = "min-w-0 rounded-chip font-medium text-fg hover:underline hover:underline-offset-2";

function columns(t: T, outcomes: ReadonlyMap<string, NodeOutcome>): Column<FleetRow>[] {
  return [
    {
      id: "app",
      header: t("fleet.column.app"),
      card: "title",
      sortValue: (row) => field(row, "domain"),
      cell: (row) => (
        <HrefLink href={originOf(row).href} className={NAME_LINK}>
          <Mono truncate>{field(row, "domain") ?? ""}</Mono>
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
      hideBelow: "md",
      card: "meta",
      cell: (row) => {
        const type = field(row, "app_type");
        return type === null ? <EmptyCell reason={t("fleet.apps.noType")} /> : <span className="text-13 text-fg">{type}</span>;
      },
    },
    {
      id: "deploy",
      header: t("fleet.column.lastDeploy"),
      hideBelow: "lg",
      card: "meta",
      cell: (row) => {
        const last = objectOf(row["last_deployment"]);
        if (last === null) return <EmptyCell reason={t("fleet.apps.neverDeployed")} />;
        const view = deployStatus(typeof last["status"] === "string" ? last["status"] : null, t.locale);
        const when = typeof last["finished_at"] === "string" ? last["finished_at"] : typeof last["started_at"] === "string" ? last["started_at"] : null;
        return (
          <span className="flex flex-wrap items-center gap-x-2 text-13">
            <span className={cx("inline-flex items-center gap-1", stateTextClass(view.state))}>
              <StatusGlyph state={view.state} size={10} />
              <span className="text-fg">{view.label}</span>
            </span>
            {when !== null ? <RelativeTime value={when} className="text-fg-muted" /> : null}
          </span>
        );
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
  const Add = ICONS.add;
  const rows = view.data?.items ?? [];
  const shown = rows.filter((row) => {
    const origin = originOf(row);
    if (search.server !== undefined && origin.node !== search.server) return false;
    if (search.state !== undefined && appStatus(field(row, "status"), t.locale).state !== search.state) return false;
    return matchesQuery([field(row, "domain"), field(row, "name"), field(row, "app_type"), origin.node], search.q);
  });

  const set = (patch: FleetSearchPatch, replace = false): void => {
    onSearchChange(patchSearch(search, patch), { replace });
  };

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
            <Link to="/apps/new" search={{ node: undefined }} className={buttonClassName("secondary")}>
              <Add aria-hidden="true" />
              {t("fleet.apps.newApplication")}
            </Link>
          </>
        }
      />
      <DataTable
        caption={t("fleet.apps.caption")}
        columns={columns(t, view.outcomes)}
        rows={shown}
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
      <CommandHint label={t("fleet.page.fromTerminal")} command="noust fleet apps" />
    </div>
  );
}
