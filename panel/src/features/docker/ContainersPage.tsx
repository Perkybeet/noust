/**
 * Server > Docker > Containers: every container Docker has on the machine, with its state, its stack and whose it is. What Noust did
 * not create is here too, and a container of an application is changed from the application. A row opens the container from the
 * inside; its menu offers what the server says works for whose it is.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { dockerContainersQuery } from "../../api/queries/docker";
import type { DockerContainer } from "../../api/queries/docker";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { ICONS } from "../../components/ui/icons";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { NodeCapabilityGate } from "../../nodes/capability";
import { ContainerMenu } from "./menus";
import { stateView } from "./model";
import { ContainerLink, ContainerPill, OwnerCell, StackLink } from "./pieces";
import { RemoveDialog } from "./RemoveDialog";
import type { RemoveSubject } from "./RemoveDialog";
import { NothingMatched, ownerMatches, textMatches, useListFilters } from "./lists";
import type { DockerListProps } from "./lists";
import { STATE_FILTERS } from "./search";
import type { StateFilter } from "./search";
import { DOCKER_CAPABILITY } from "./SummaryPage";
import { Unavailable } from "./Unavailable";

const ANY = "all";

/** Whether a container is in the state a filter asks for: running, a problem (what asks for the operator), or not running. */
export function stateMatches(container: Pick<DockerContainer, "state" | "state_kind" | "problem">, wanted: StateFilter | undefined): boolean {
  if (wanted === undefined) return true;
  if (wanted === "problem") return container.problem;
  const up = container.state === "running" || container.state === "restarting" || container.state === "paused";
  return wanted === "running" ? up : !up;
}

/** The containers a list shows once the page's filters are applied. The application's own filter is the link its page draws. */
export function visibleContainers(items: readonly DockerContainer[], search: DockerListProps["search"]): DockerContainer[] {
  return items.filter(
    (item) =>
      textMatches(search.q, item.name, item.image, item.project) &&
      ownerMatches(item.owner, search.owner) &&
      stateMatches(item, search.state) &&
      (search.app === undefined || (item.owner.kind === "app" && item.owner.subject === search.app)) &&
      (search.project === undefined || item.project === search.project),
  );
}

export function ContainersPage({ search, onSearchChange }: DockerListProps) {
  const t = useT();
  const query = useQuery(dockerContainersQuery());
  const data = query.data;
  const [removing, setRemoving] = useState<RemoveSubject | null>(null);
  const { searchBox, owner } = useListFilters({ search, onSearchChange }, t("docker.containers.searchLabel"));
  const rows = useMemo(() => (data?.items === undefined ? undefined : visibleContainers(data.items, search)), [data, search]);
  const clear = (): void => onSearchChange({});
  const columns: Column<DockerContainer>[] = [
    { id: "name", header: t("docker.columns.container"), card: "title", sortValue: (row) => row.name, cell: (row) => <ContainerLink name={row.name} /> },
    {
      id: "state",
      header: t("docker.columns.state"),
      card: "status",
      width: "w-44",
      sortValue: (row) => stateView(row.state_kind).word,
      cell: (row) => <ContainerPill kind={row.state_kind} />,
    },
    { id: "stack", header: t("docker.columns.stack"), card: "meta", sortValue: (row) => row.project, cell: (row) => (row.project === "" ? t("docker.columns.noStack") : <StackLink name={row.project} />) },
    { id: "owner", header: t("docker.columns.owner"), card: "meta", cell: (row) => <OwnerCell owner={row.owner} /> },
    { id: "image", header: t("docker.columns.image"), card: "hidden", hideBelow: "xl", sortValue: (row) => row.image, cell: (row) => <Mono truncate>{row.image}</Mono> },
  ];
  const states = [{ value: ANY, label: t("docker.filters.allStates") }, ...STATE_FILTERS.map((state) => ({ value: state, label: state === "running" ? t("common.statusPill.running") : state === "problem" ? t("docker.filters.state.problem") : t("docker.filters.state.stopped") }))];
  const shown = rows?.length ?? 0;
  const total = data?.items?.length ?? 0;
  return (
    <NodeCapabilityGate capability={DOCKER_CAPABILITY}>
      <ListPage
        header={{ title: t("docker.containers.title"), description: t("docker.containers.description") }}
        filters={
          <FilterBar
            label={t("docker.containers.filterLabel")}
            search={searchBox}
            filters={
              <>
                <Select
                  aria-label={t("docker.filters.stateLabel")}
                  options={states}
                  value={search.state ?? ANY}
                  onValueChange={(value) => onSearchChange({ ...search, state: value === ANY ? undefined : (value as StateFilter) })}
                />
                {owner}
                {search.app !== undefined ? (
                  <Button size="sm" variant="secondary" icon={<ICONS.dismiss aria-hidden="true" />} onClick={() => onSearchChange({ ...search, app: undefined })}>
                    {t("docker.filters.onlyApp", { app: search.app })}
                  </Button>
                ) : null}
              </>
            }
            count={data?.items === undefined ? undefined : t("docker.containers.count", { shown, total })}
          />
        }
      >
        {query.isError && data === undefined ? (
          <ErrorBlock error={query.error} title={t("docker.containers.failed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
        ) : data !== undefined && !data.available ? (
          <Unavailable engine={data.engine} />
        ) : (
          <DataTable
            caption={t("docker.containers.caption")}
            columns={columns}
            rows={rows}
            getRowId={(row) => row.id}
            skeletonRows={8}
            pagination="docker-containers"
            rowActions={(row) => <ContainerMenu container={row} onRemove={setRemoving} />}
            empty={
              total === 0 ? <EmptyState variant="firstUse" icon={<OBJECT_ICONS.container aria-hidden="true" />} title={t("docker.containers.empty.title")} description={t("docker.containers.empty.description")} /> : <NothingMatched onClear={clear} />
            }
          />
        )}
      </ListPage>
      <RemoveDialog subject={removing} onClose={() => setRemoving(null)} />
    </NodeCapabilityGate>
  );
}
