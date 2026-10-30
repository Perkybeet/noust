import { useMemo } from "react";

import type { AlertRule } from "../../api/queries/alerts";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Badge } from "../../components/ui/Badge";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { StatusPill } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { field, originOf } from "../fleet/data";
import type { FleetRow, NodeOutcome } from "../fleet/data";
import { isFiltered, matchesQuery, patchSearch } from "../fleet/filters";
import type { FleetSearch, FleetSearchPatch } from "../fleet/filters";
import { HrefLink } from "../fleet/links";
import { ServerCell, StateFilter } from "../fleet/parts";
import { ServerScope } from "../operation/ServerScope";
import { ViewNotices } from "../operation/ViewNotices";
import { useView } from "../operation/views";
import { acknowledgement } from "../settings/alerts/data";
import { formatReading, ruleSentence } from "../settings/alerts/rules";
import { stateOf } from "../settings/alerts/RuleList";
import { namedActor } from "../activity/data";
import { textLinkClassName } from "../../components/ui/TextLink";

const STATES = ["firing", "ok", "off"] as const;
type AlertState = (typeof STATES)[number];

/** A row of the view is the rule as its server lists it. */
function ruleOf(row: FleetRow): AlertRule {
  return row as unknown as AlertRule;
}

const NAME_LINK = textLinkClassName("inline", "min-w-0", "row");

/** What is firing in a rule, one line each: who it is about, what it reads and since when, and who took it. */
function Firing({ rule }: { rule: AlertRule }) {
  const t = useT();
  const firing = (rule.states ?? []).filter((entry) => entry.state === "firing");
  if (!rule.enabled || firing.length === 0) return <EmptyCell reason={t("alerts.fleet.nothingFiring")} />;
  return (
    <ul className="flex min-w-0 flex-col gap-1 text-12 text-fg-muted">
      {firing.map((entry) => {
        const taken = acknowledgement(entry);
        return (
          <li key={entry.subject} className="flex min-w-0 flex-wrap items-center gap-x-2 gap-y-0.5">
            <span className="min-w-0">
              {t.rich("alerts.rules.firingLine", {
                subject: entry.subject === "" ? t("alerts.rules.server") : <Mono key="subject">{entry.subject}</Mono>,
                value: entry.last_value === null || entry.last_value === undefined ? "-" : formatReading(rule.unit, entry.last_value, t.locale),
                since: <RelativeTime key="since" value={entry.since} />,
              })}
            </span>
            {taken !== null ? <Badge>{t("alerts.fleet.takenBy", { who: namedActor(t, taken.by) })}</Badge> : null}
          </li>
        );
      })}
    </ul>
  );
}

function columns(t: T, outcomes: ReadonlyMap<string, NodeOutcome>, withServer: boolean): Column<FleetRow>[] {
  return [
    {
      id: "rule",
      header: t("alerts.fleet.column.rule"),
      card: "title",
      sortValue: (row) => field(row, "name"),
      cell: (row) => {
        const rule = ruleOf(row);
        return (
          <span className="flex min-w-0 flex-col">
            <HrefLink href={originOf(row).href} className={NAME_LINK}>
              <Mono truncate>{rule.name}</Mono>
            </HrefLink>
            <span className="text-12 text-fg-muted">{ruleSentence(t, rule, rule.unit)}</span>
          </span>
        );
      },
    },
    {
      id: "state",
      header: t("fleet.column.state"),
      card: "status",
      width: "w-32",
      cell: (row) => {
        const state = stateOf(ruleOf(row));
        return <StatusPill state={state.status} label={t(`alerts.rules.state.${state.key}`)} appearance="inline" size="sm" />;
      },
    },
    ...(withServer
      ? [
          {
            id: "server",
            header: t("fleet.column.server"),
            card: "meta" as const,
            sortValue: (row: FleetRow) => originOf(row).node,
            cell: (row: FleetRow) => <ServerCell origin={originOf(row)} outcome={outcomes.get(originOf(row).node)} />,
          },
        ]
      : []),
    {
      id: "now",
      header: t("alerts.fleet.column.now"),
      card: "meta",
      cell: (row) => <Firing rule={ruleOf(row)} />,
    },
  ];
}

export interface FleetAlertsProps {
  search: FleetSearch;
  onSearchChange: (search: FleetSearch, options?: { replace?: boolean }) => void;
}

/**
 * The alert rules of every server (T1), the ones firing first, each naming its server and what is firing
 * in it. It is the page of Alerts with the servers' column and their filter: editing a rule or taking an
 * alert happens on the server the row opens, where the rule lives; this is where to see which server
 * needs somebody.
 */
export function FleetAlerts({ search, onSearchChange }: FleetAlertsProps) {
  const t = useT();
  const view = useView("alerts", { nodes: search.server !== undefined ? [search.server] : [] });
  const rows = useMemo(() => view.data?.items ?? [], [view.data]);
  const shown = rows.filter((row) => (search.state === undefined || stateOf(ruleOf(row)).key === search.state) && matchesQuery([field(row, "name"), originOf(row).node, field(row, "app_domain")], search.q));
  const firing = rows.filter((row) => row["firing"] === true).length;

  const set = (patch: FleetSearchPatch, replace = false): void => {
    onSearchChange(patchSearch(search, patch), { replace });
  };

  let content;
  // design-exception: hand-error-ternary the table draws its own skeleton rows; the failure replaces only what it would have listed
  if (view.isError && view.data === undefined) {
    content = <ErrorBlock error={view.error} title={t("alerts.fleet.loadFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />;
  } else if (view.data !== undefined && rows.length === 0 && !isFiltered(search)) {
    content = <EmptyState variant="firstUse" icon={<OBJECT_ICONS.alert />} title={t("alerts.rules.emptyTitle")} description={t("alerts.rules.emptyDescription")} command="noust alert list" />;
  } else {
    content = (
      <DataTable
        caption={t("alerts.fleet.caption")}
        columns={columns(t, view.outcomes, view.data?.fleet === true)}
        rows={shown}
        getRowId={(row) => `${originOf(row).node}:${field(row, "name") ?? ""}`}
        loading={view.isPending}
        skeletonRows={4}
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
                {t("common.words.clearFilters")}
              </Button>
            }
          />
        }
      />
    );
  }

  return (
    <ListPage
      header={{ title: t("nav.alerts.label"), description: t("alerts.page.fleet") }}
      notice={<ViewNotices view={view.data} resource="alerts" />}
      filters={
        <FilterBar
          label={t("alerts.fleet.filterLabel")}
          search={{
            value: search.q ?? "",
            onChange: (value) => {
              set({ q: value }, true);
            },
            label: t("alerts.fleet.searchLabel"),
            placeholder: t("alerts.fleet.searchPlaceholder"),
          }}
          filters={
            <>
              <ServerScope
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
                options={STATES.map((state: AlertState) => ({ value: state, label: t(`alerts.rules.state.${state}`) }))}
              />
            </>
          }
          count={view.data === undefined ? "" : firing === 0 ? t("alerts.rules.allClear") : t("alerts.rules.firingCount", { count: firing })}
        />
      }
    >
      {content}
    </ListPage>
  );
}
