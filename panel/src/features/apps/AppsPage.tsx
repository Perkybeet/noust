import { useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";
import { Boxes, Plus, Search, X } from "lucide-react";
import { useMemo } from "react";

import { appsQuery } from "../../api/queries/apps";
import { PageHeader } from "../../app/PageHeader";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { STATUS } from "../../components/ui/StatusPill";
import { Button, buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { Input } from "../../components/ui/Input";
import { Kbd } from "../../components/ui/Kbd";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { AppRowActions } from "./AppRowActions";
import { AppsTable } from "./AppsTable";
import { latestDeployByDomain, recentDeploysQuery, useLatestMetrics } from "./data";
import { STATE_FILTERS, appTypes, filterApps, isFiltered } from "./filters";
import type { AppsSearch } from "./filters";
import { useStateTransitions } from "./useStateTransitions";

const ALL = "all";

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

function NewAppLink() {
  const t = useT();
  return (
    <Link to="/apps/new" className={buttonClassName("primary")}>
      <Plus aria-hidden="true" />
      {t("apps.newApplication")}
    </Link>
  );
}

/**
 * Every application on the machine in one table: searchable with `/`, filtered by state and
 * type through the URL, each row opening its app or acting on it from its menu.
 */
export function AppsPage({ search, onSearchChange }: AppsPageProps) {
  const t = useT();
  const apps = useQuery(appsQuery());
  const deploys = useQuery(recentDeploysQuery());
  const metrics = useLatestMetrics();

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

  const filtered = isFiltered(search);
  const count =
    apps.data === undefined
      ? null
      : filtered
        ? t("apps.page.countFiltered", { shown: shown.length, total: all.length })
        : t("apps.page.count", { count: all.length });

  return (
    <>
      <PageHeader title={t("apps.page.title")} description={t("apps.page.description")} actions={<NewAppLink />} />

      {apps.isError && apps.data === undefined ? (
        <ErrorBlock
          error={apps.error}
          title={t("apps.page.couldNotLoad")}
          onRetry={() => void apps.refetch()}
          retrying={apps.isRefetching}
        />
      ) : apps.data !== undefined && all.length === 0 ? (
        <EmptyState
          level={2}
          icon={<Boxes />}
          title={t("apps.page.emptyTitle")}
          description={t("apps.page.emptyDescription")}
          action={<NewAppLink />}
          command="wasm create -d example.com -s https://github.com/you/app"
          className="py-16"
        />
      ) : (
        <div className="flex flex-col gap-4">
          <div role="search" aria-label={t("apps.page.filterLabel")} className="flex flex-wrap items-end gap-2">
            <Input
              type="search"
              aria-label={t("apps.page.searchLabel")}
              placeholder={t("apps.page.searchPlaceholder")}
              data-page-search=""
              value={search.q ?? ""}
              onValueChange={(value: string) => set({ q: value }, true)}
              icon={<Search />}
              // The shortcut is for keyboards; a phone has no use for the hint.
              suffix={search.q ? undefined : <Kbd className="pointer-coarse:hidden">/</Kbd>}
              className="w-full sm:w-72"
              autoComplete="off"
              spellCheck={false}
            />
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
              options={[{ value: ALL, label: t("apps.page.everyType") }, ...types.map((type) => ({ value: type, label: type }))]}
              className="min-w-36"
            />
            {filtered ? (
              <Button variant="ghost" icon={<X aria-hidden="true" />} onClick={() => onSearchChange({})}>
                {t("apps.page.clearFilters")}
              </Button>
            ) : null}
            <p role="status" className="ml-auto self-center text-13 text-fg-muted">
              {count ?? ""}
            </p>
          </div>

          <AppsTable
            apps={shown}
            deploys={latest}
            metrics={metrics}
            caption={filtered ? t("apps.page.captionFiltered") : t("apps.page.caption")}
            loading={apps.isPending}
            detail="full"
            rowActions={(app) => <AppRowActions app={app} />}
            empty={
              <EmptyState
                title={t("apps.page.noMatchTitle")}
                description={t("apps.page.noMatchDescription")}
                action={
                  <Button icon={<X aria-hidden="true" />} onClick={() => onSearchChange({})}>
                    {t("apps.page.clearFilters")}
                  </Button>
                }
                className="border-0 py-8"
              />
            }
          />
          {/* Drawn with the rows, not before: under a list of unknown length it would only be
              pushed down the page when they arrive. */}
          {apps.isPending ? null : <CommandHint command="wasm list" label={t("apps.page.fromTerminal")} />}
        </div>
      )}
    </>
  );
}
