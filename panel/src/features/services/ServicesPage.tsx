import { useQuery } from "@tanstack/react-query";
import { Cog } from "lucide-react";
import { useMemo, useState } from "react";

import { appsQuery } from "../../api/queries/apps";
import { servicesQuery } from "../../api/queries/services";
import { CommandHint } from "../../components/page/CommandHint";
import { FilterBar } from "../../components/page/FilterBar";
import { ErrorBlock } from "../../components/page/QueryState";
import { SegmentedControl } from "../../components/page/SegmentedControl";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { TabToolbar } from "../server/TabToolbar";
import { CreateServiceDialog } from "./CreateServiceDialog";
import { ServiceRowActions } from "./ServiceRowActions";
import { ServicesTable } from "./ServicesTable";
import { appOfUnit, filterServices, isFiltered } from "./data";
import type { ServiceInfo, ServiceStateFilter, ServicesSearch } from "./data";

/** A change to the filters: a key set to undefined is cleared. */
type SearchPatch = { [K in keyof ServicesSearch]?: ServicesSearch[K] | undefined };

type Scope = "noust" | "all";
/** Select's value for "no state filter". */
const ANY = "any";

export interface ServicesPageProps {
  search: ServicesSearch;
  onSearchChange: (search: ServicesSearch, options?: { replace?: boolean }) => void;
}

/**
 * The Services tab of the Server area: the systemd units Noust created on this machine, and on
 * request every unit on it (`noust_only=false`), each marked Noust's or foreign (foreign ones are
 * read only: see `ServiceRowActions`). A unit an application runs names the application.
 */
export function ServicesPage({ search, onSearchChange }: ServicesPageProps) {
  const t = useT();
  const showAll = search.all === true;
  const services = useQuery(servicesQuery(!showAll));
  const apps = useQuery(appsQuery());
  const [createOpen, setCreateOpen] = useState(false);

  const fetched = useMemo(() => services.data?.services ?? [], [services.data]);
  const shown = useMemo(() => filterServices(fetched, search), [fetched, search]);
  const appList = apps.data?.apps ?? [];
  const appOf = (service: ServiceInfo): string | null => appOfUnit(appList, service.name)?.domain ?? null;

  const set = (patch: SearchPatch, replace = false): void => {
    const next: SearchPatch = { ...search, ...patch };
    const clean: ServicesSearch = {};
    if (next.q) clean.q = next.q;
    if (next.all) clean.all = true;
    if (next.state) clean.state = next.state;
    onSearchChange(clean, { replace });
  };

  // "Clear filters" clears the text and the state: the scope is what is listed, not a filter
  // on it, so it stays as the operator set it.
  const clearFilters = (): void => onSearchChange(showAll ? { all: true } : {});
  const filtered = isFiltered(search);
  const newService = (
    <Button variant="primary" icon={<ICONS.add aria-hidden="true" />} onClick={() => setCreateOpen(true)}>
      {t("services.page.newService")}
    </Button>
  );

  return (
    <div className="flex min-w-0 flex-col gap-4">
      <TabToolbar summary={t("services.page.description")} actions={newService} />
      {services.isError && services.data === undefined ? (
        <ErrorBlock error={services.error} title={t("services.page.loadFailed")} onRetry={() => void services.refetch()} retrying={services.isRefetching} />
      ) : services.data !== undefined && fetched.length === 0 && !showAll ? (
        <EmptyState
          variant="firstUse"
          level={2}
          icon={<Cog />}
          title={t("services.page.emptyTitle")}
          description={t("services.page.emptyDescription")}
          action={
            <Button onClick={() => set({ all: true }, true)} variant="secondary">
              {t("services.page.showSystem")}
            </Button>
          }
          command="noust service create --name worker --command '/usr/bin/node worker.js' --directory /var/www/worker"
        />
      ) : (
        <>
          <FilterBar
            label={t("services.page.filterLabel")}
            search={{
              label: t("services.page.searchLabel"),
              placeholder: t("services.page.searchPlaceholder"),
              value: search.q ?? "",
              onChange: (q) => set({ q }, true),
            }}
            filters={
              <>
                <SegmentedControl<Scope>
                  label={t("services.page.scopeLabel")}
                  value={showAll ? "all" : "noust"}
                  onValueChange={(scope) => set({ all: scope === "all" ? true : undefined }, true)}
                  options={[
                    { value: "noust", label: t("services.page.scopeNoust") },
                    { value: "all", label: t("services.page.scopeAll") },
                  ]}
                />
                <Select<ServiceStateFilter | typeof ANY>
                  aria-label={t("services.page.stateLabel")}
                  value={search.state ?? ANY}
                  onValueChange={(state) => set({ state: state === ANY ? undefined : state }, true)}
                  options={[
                    { value: ANY, label: t("services.page.stateAny") },
                    { value: "failed", label: t("services.page.stateFailed") },
                    { value: "running", label: t("services.page.stateRunning") },
                    { value: "stopped", label: t("services.page.stateStopped") },
                  ]}
                />
              </>
            }
            count={
              services.data === undefined
                ? ""
                : filtered
                  ? t("services.page.countFiltered", { shown: shown.length, total: fetched.length })
                  : t("services.page.count", { count: fetched.length })
            }
          />
          <ServicesTable
            services={shown}
            caption={filtered ? t("services.page.tableCaptionFiltered") : t("services.page.tableCaption")}
            loading={services.isPending}
            rowActions={(service) => <ServiceRowActions service={service} />}
            appOf={appOf}
            empty={
              <EmptyState
                variant="inline"
                title={t("services.page.noMatchTitle")}
                action={
                  <Button size="sm" onClick={clearFilters}>
                    {t("services.page.clearFilters")}
                  </Button>
                }
              />
            }
          />
        </>
      )}
      {/* Drawn with the rows, not before: under a list of unknown length it would only be
          pushed down the page when they arrive. */}
      {services.isPending ? null : <CommandHint command="noust service list" label={t("services.fromTerminal")} />}
      <CreateServiceDialog open={createOpen} onOpenChange={setCreateOpen} />
    </div>
  );
}
