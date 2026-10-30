import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Play, X } from "lucide-react";
import { useMemo } from "react";

import { appsViewQuery } from "../../api/queries/views";
import { LinkTabs } from "../../app/LinkTabs";
import { tabsOf } from "../../app/navCore";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button, buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { Select } from "../../components/ui/Select";
import { STATUS, StatusGlyph } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { STATE_FILTERS, appTagNames, appTypes, filterApps, isFiltered } from "../apps/filters";
import type { AppsSearch } from "../apps/filters";
import { AppsTable } from "../apps/AppsTable";
import { useTypeName } from "../apps/data";
import type { Deployment } from "../apps/data";
import { filterByOwner } from "../teams/organization";
import { FLEET_APPS_SCREENS } from "../nav/fleet";
import { fleetApps, placesOf, serverSegments } from "./appsView";
import { useFleetActions } from "./BulkActionDialog";
import { PartialNotice } from "./parts";
import type { NodeOutcome } from "./data";
import { ViewRefusals } from "./ViewRefusals";

const ALL = "all";

/** A change to the filters: a key set to undefined is cleared. */
type SearchPatch = { [K in keyof AppsSearch]?: AppsSearch[K] | undefined };

export interface FleetAppsPageProps {
  search: AppsSearch;
  onSearchChange: (search: AppsSearch, options?: { replace?: boolean }) => void;
}

/** A fleet row carries its own newest deploy (`last_deployment`), so the table needs no history to read it from. */
const NO_DEPLOYS: ReadonlyMap<string, Deployment> = new Map();

/** More servers than a strip can hold without wrapping to a second line: the choice is a select instead. */
const STRIP_UP_TO = 6;

/**
 * Every application of every server, as the same list a server has: one table, one set of filters, the
 * server as a column and as the first filter, chosen on a strip that says how many each runs and which has a
 * problem. Choosing a server filters, it does not move to another page (the address says `?server=`). A
 * server that did not answer, or that does not let this account read, is said once above the rows, and its
 * rows are the last it gave (with their age) or absent: the page is never blank because one server is down.
 */
export function FleetAppsPage({ search, onSearchChange }: FleetAppsPageProps) {
  const t = useT();
  const view = useQuery(appsViewQuery());
  const typeName = useTypeName();
  const actions = useFleetActions();
  const all = useMemo(() => fleetApps(view.data), [view.data]);
  const segments = useMemo(() => serverSegments(view.data, all, t.locale), [view.data, all, t.locale]);
  const outcomes = useMemo(() => new Map((view.data?.nodes ?? []).map((node) => [node.name, node as unknown as NodeOutcome])), [view.data]);
  const places = useMemo(() => placesOf(all), [all]);
  const types = useMemo(() => appTypes(all), [all]);
  const tags = useMemo(() => appTagNames(all), [all]);

  const onServer = useMemo(() => (search.server === undefined ? all : all.filter((app) => app.node === search.server)), [all, search.server]);
  const shown = useMemo(() => filterByOwner(filterApps(onServer, search), { project: search.project ?? null, environment: search.environment ?? null }), [onServer, search]);

  const set = (patch: SearchPatch, replace = false): void => {
    const next: SearchPatch = { ...search, ...patch };
    const clean: AppsSearch = {};
    if (next.q) clean.q = next.q;
    if (next.server) clean.server = next.server;
    if (next.state) clean.state = next.state;
    if (next.type) clean.type = next.type;
    if (next.tag) clean.tag = next.tag;
    if (next.project) clean.project = next.project;
    if (next.environment) clean.environment = next.environment;
    onSearchChange(clean, { replace });
  };
  const clear = (): void => {
    onSearchChange({});
  };

  const Add = ICONS.add;
  const header = {
    title: t("apps.page.title"),
    primaryAction: (
      <Link to="/apps/new" search={{ node: undefined }} className={buttonClassName("primary")}>
        <Add aria-hidden="true" />
        {t("apps.newApplication")}
      </Link>
    ),
  };
  const tabs = <LinkTabs label={t("fleet.page.tabsLabel")} tabs={tabsOf(FLEET_APPS_SCREENS)} />;

  // design-exception: hand-error-ternary the failure replaces the list but the page's own header and tabs stay around it, as in the applications page
  if (view.isError && view.data === undefined) {
    return (
      <ListPage header={header} tabs={tabs}>
        <ErrorBlock error={view.error} title={t("fleet.page.loadFailed")} onRetry={() => void view.refetch()} retrying={view.isRefetching} />
      </ListPage>
    );
  }

  const filtered = isFiltered(search) || search.server !== undefined;
  const count = view.data === undefined ? "" : filtered ? t("apps.page.countFiltered", { shown: shown.length, total: all.length }) : t("apps.page.count", { count: all.length });
  const strip = segments.length > 1 && segments.length <= STRIP_UP_TO;
  const serverFilter = !strip && segments.length > 1;

  return (
    <ListPage
      header={header}
      tabs={tabs}
      notice={
        view.data !== undefined && (view.data.partial || view.data.refusals.length > 0) ? (
          <div className="flex flex-col gap-3">
            <PartialNotice view={view.data} />
            <ViewRefusals refusals={view.data.refusals} what={t("fleet.view.apps")} />
          </div>
        ) : undefined
      }
      filters={
        <div className="flex min-w-0 flex-col gap-3">
          {strip ? (
            <div>
            <SegmentedControl
              label={t("fleet.filters.server")}
              value={search.server ?? ALL}
              onValueChange={(next) => set({ server: next === ALL ? undefined : next })}
              options={[
                { value: ALL, label: t("fleet.filters.wholeFleet"), count: all.length },
                ...segments.map((segment) => ({
                  value: segment.name,
                  label: segment.name,
                  count: segment.count,
                  ...(segment.worst !== null ? { icon: <StatusGlyph state={segment.worst} size={12} /> } : {}),
                })),
              ]}
            />
            </div>
          ) : null}
          <FilterBar
            label={t("apps.page.filterLabel")}
            search={{ value: search.q ?? "", onChange: (value) => set({ q: value }, true), label: t("apps.page.searchLabel"), placeholder: t("apps.page.searchPlaceholder") }}
            filters={
              <>
                {serverFilter ? (
                  <Select
                    aria-label={t("fleet.filters.server")}
                    value={search.server ?? ALL}
                    onValueChange={(value) => set({ server: value === ALL ? undefined : value })}
                    options={[{ value: ALL, label: t("fleet.filters.everyServer") }, ...segments.map((segment) => ({ value: segment.name, label: segment.name }))]}
                    mono={false}
                    className="min-w-36"
                  />
                ) : null}
                <Select
                  aria-label={t("apps.page.stateLabel")}
                  value={search.state ?? ALL}
                  onValueChange={(value) => set({ state: STATE_FILTERS.find((state) => state === value) })}
                  options={[{ value: ALL, label: t("apps.page.everyState") }, ...STATE_FILTERS.map((state) => ({ value: state, label: t(STATUS[state].labelKey) }))]}
                  className="min-w-36"
                />
                <Select
                  aria-label={t("apps.page.typeLabel")}
                  value={search.type ?? ALL}
                  onValueChange={(value) => set({ type: value === ALL ? undefined : value })}
                  options={[{ value: ALL, label: t("apps.page.everyType") }, ...types.map((type) => ({ value: type, label: typeName(type) ?? type }))]}
                  className="min-w-36"
                />
                {tags.length > 0 || search.tag !== undefined ? (
                  <Select
                    aria-label={t("appTags.filter.label")}
                    value={search.tag ?? ALL}
                    onValueChange={(value) => set({ tag: value === ALL ? undefined : value })}
                    options={[{ value: ALL, label: t("appTags.filter.every") }, ...tags.map((tag) => ({ value: tag, label: tag }))]}
                    className="min-w-36"
                  />
                ) : null}
                {/* Where applications live is told only where there is more than one place to tell apart. */}
                {places.projects.length > 1 ? (
                  <Select
                    aria-label={t("teams.filters.projectLabel")}
                    value={search.project ?? ALL}
                    onValueChange={(value) => set({ project: value === ALL ? undefined : value })}
                    options={[{ value: ALL, label: t("teams.filters.everyProject") }, ...places.projects.map((slug) => ({ value: slug, label: slug }))]}
                    className="min-w-36"
                  />
                ) : null}
                {places.environments.length > 1 ? (
                  <Select
                    aria-label={t("teams.filters.environmentLabel")}
                    value={search.environment ?? ALL}
                    onValueChange={(value) => set({ environment: value === ALL ? undefined : value })}
                    options={[{ value: ALL, label: t("teams.filters.everyEnvironment") }, ...places.environments.map((slug) => ({ value: slug, label: slug }))]}
                    className="min-w-36"
                  />
                ) : null}
              </>
            }
            count={count}
            actions={
              <>
                {filtered ? (
                  <Button variant="ghost" icon={<X aria-hidden="true" />} onClick={clear}>
                    {t("common.words.clearFilters")}
                  </Button>
                ) : null}
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
        </div>
      }
      {...(view.isPending ? {} : { footer: <CommandHint command="noust fleet apps" label={t("common.words.fromTerminal")} /> })}
    >
      <AppsTable
        apps={shown}
        deploys={NO_DEPLOYS}
        metrics={undefined}
        fleet={{ outcomes }}
        caption={filtered ? t("apps.page.captionFiltered") : t("apps.page.caption")}
        loading={view.isPending}
        skeletonRows={6}
        empty={
          view.data !== undefined && all.length === 0 ? (
            <EmptyState variant="inline" title={t("fleet.apps.empty")} />
          ) : (
            <EmptyState
              variant="inline"
              title={t("apps.page.noMatchTitle")}
              action={
                <Button size="sm" variant="ghost" onClick={clear}>
                  {t("common.words.clearFilters")}
                </Button>
              }
            />
          )
        }
      />
    </ListPage>
  );
}
