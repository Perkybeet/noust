import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Boxes, X } from "lucide-react";
import { useMemo } from "react";

import { appsQuery } from "../../api/queries/apps";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button, buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { Select } from "../../components/ui/Select";
import { STATUS } from "../../components/ui/StatusPill";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";
import { AppRowActions } from "./AppRowActions";
import { AppsTable } from "./AppsTable";
import { latestDeployByDomain, recentDeploysQuery, useLatestMetrics, useTypeName } from "./data";
import { STATE_FILTERS, appTypes, filterApps, isFiltered } from "./filters";
import type { AppsSearch } from "./filters";
import { useStateTransitions } from "./useStateTransitions";

const ALL = "all";

/** Rows drawn while the list loads: a typical server's worth, so the footer does not jump far. */
const SKELETON_ROWS = 8;

/** A change to the filters: a key set to undefined is cleared. */
type SearchPatch = { [K in keyof AppsSearch]?: AppsSearch[K] | undefined };

export interface AppsPageProps {
  search: AppsSearch;
  /**
   * Sets the filters in the URL. `replace` for typing, so the history does not gain an entry
   * per keystroke; choosing a filter pushes one, so Back undoes it.
   */
  onSearchChange: (search: AppsSearch, options?: { replace?: boolean }) => void;
}

/** "New application": the page's one primary action, and the empty state's way forward. */
function NewAppLink({ variant = "primary" }: { variant?: "primary" | "secondary" }) {
  const t = useT();
  const Add = ICONS.add;
  return (
    <Link to="/apps/new" className={buttonClassName(variant)}>
      <Add aria-hidden="true" />
      {t("apps.newApplication")}
    </Link>
  );
}

/**
 * Every application on the server, as a T1 list: the domain first and its state beside it,
 * searchable with `/` and filtered by state and type through the URL, each row opening its app
 * or acting on it from its menu. On a phone the rows are cards with the menu always in view.
 */
export function AppsPage({ search, onSearchChange }: AppsPageProps) {
  const t = useT();
  const { node } = useNode();
  const apps = useQuery(appsQuery());
  const deploys = useQuery(recentDeploysQuery());
  const metrics = useLatestMetrics();
  const typeName = useTypeName();

  const all = useMemo(() => apps.data?.apps ?? [], [apps.data]);
  const shown = useMemo(() => filterApps(all, search), [all, search]);
  const latest = useMemo(() => latestDeployByDomain(deploys.data?.items ?? []), [deploys.data]);
  const types = useMemo(() => appTypes(all), [all]);
  useStateTransitions(apps.data?.apps);

  const set = (patch: SearchPatch, replace = false): void => {
    const next: SearchPatch = { ...search, ...patch };
    const clean: AppsSearch = {};
    if (next.q) clean.q = next.q;
    if (next.state) clean.state = next.state;
    if (next.type) clean.type = next.type;
    onSearchChange(clean, { replace });
  };
  const clear = (): void => {
    onSearchChange({});
  };

  const filtered = isFiltered(search);
  const firstUse = apps.data !== undefined && all.length === 0;
  const count =
    apps.data === undefined
      ? ""
      : filtered
        ? t("apps.page.countFiltered", { shown: shown.length, total: all.length })
        : t("apps.page.count", { count: all.length });

  const header = {
    title: t("apps.page.title"),
    description: t("apps.page.description"),
    server: node,
    primaryAction: <NewAppLink />,
  };

  if (apps.isError && apps.data === undefined) {
    return (
      <ListPage header={header}>
        <ErrorBlock error={apps.error} title={t("apps.page.couldNotLoad")} onRetry={() => void apps.refetch()} retrying={apps.isRefetching} />
      </ListPage>
    );
  }

  if (firstUse) {
    return (
      <ListPage header={header}>
        <EmptyState
          variant="firstUse"
          icon={<Boxes />}
          title={t("apps.page.emptyTitle")}
          description={t("apps.page.emptyDescription")}
          action={<NewAppLink variant="secondary" />}
          command="noust create --domain example.com --source https://github.com/you/app.git"
        />
      </ListPage>
    );
  }

  return (
    <ListPage
      header={header}
      filters={
        <FilterBar
          label={t("apps.page.filterLabel")}
          search={{
            value: search.q ?? "",
            onChange: (value) => set({ q: value }, true),
            label: t("apps.page.searchLabel"),
            placeholder: t("apps.page.searchPlaceholder"),
          }}
          filters={
            <>
              <Select
                aria-label={t("apps.page.stateLabel")}
                value={search.state ?? ALL}
                onValueChange={(value) => set({ state: STATE_FILTERS.find((state) => state === value) })}
                options={[
                  { value: ALL, label: t("apps.page.everyState") },
                  ...STATE_FILTERS.map((state) => ({ value: state, label: t(STATUS[state].labelKey) })),
                ]}
                className="min-w-36"
              />
              <Select
                aria-label={t("apps.page.typeLabel")}
                value={search.type ?? ALL}
                onValueChange={(value) => set({ type: value === ALL ? undefined : value })}
                options={[{ value: ALL, label: t("apps.page.everyType") }, ...types.map((type) => ({ value: type, label: typeName(type) ?? type }))]}
                className="min-w-36"
              />
            </>
          }
          count={count}
          {...(filtered
            ? {
                actions: (
                  <Button variant="ghost" icon={<X aria-hidden="true" />} onClick={clear}>
                    {t("apps.page.clearFilters")}
                  </Button>
                ),
              }
            : {})}
        />
      }
      // Drawn with the rows, not before: under a list of unknown length it would only be pushed
      // down the page when they arrive.
      {...(apps.isPending ? {} : { footer: <CommandHint command="noust list" label={t("apps.page.fromTerminal")} /> })}
    >
      <AppsTable
        apps={shown}
        deploys={latest}
        metrics={metrics}
        caption={filtered ? t("apps.page.captionFiltered") : t("apps.page.caption")}
        loading={apps.isPending}
        skeletonRows={SKELETON_ROWS}
        rowActions={(app) => <AppRowActions app={app} />}
        empty={
          <EmptyState
            variant="inline"
            title={t("apps.page.noMatchTitle")}
            action={
              <Button size="sm" variant="ghost" onClick={clear}>
                {t("apps.page.clearFilters")}
              </Button>
            }
          />
        }
      />
    </ListPage>
  );
}
