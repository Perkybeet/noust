/**
 * Server > Docker > Networks: the networks Docker has, who is on each one and whose it is. Docker's own (bridge, host, none) are
 * said to be Docker's and offer nothing: they cannot be removed, and the menu does not pretend. Seeing who is on a network is what
 * answers "why can this container reach that one".
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { dockerNetworksQuery } from "../../api/queries/docker";
import type { DockerNetwork } from "../../api/queries/docker";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { Badge } from "../../components/ui/Badge";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { NodeCapabilityGate } from "../../nodes/capability";
import { NetworkMenu } from "./menus";
import { NothingMatched, ownerMatches, textMatches, useListFilters } from "./lists";
import type { DockerListProps } from "./lists";
import { OwnerCell } from "./pieces";
import { RemoveDialog } from "./RemoveDialog";
import type { RemoveSubject } from "./RemoveDialog";
import { DOCKER_CAPABILITY } from "./SummaryPage";
import { Unavailable } from "./Unavailable";

export function visibleNetworks(items: readonly DockerNetwork[], search: DockerListProps["search"]): DockerNetwork[] {
  return items.filter((item) => textMatches(search.q, item.name, item.driver, item.project, ...(item.containers ?? [])) && ownerMatches(item.owner, search.owner) && (search.project === undefined || item.project === search.project));
}

export function NetworksPage({ search, onSearchChange }: DockerListProps) {
  const t = useT();
  const query = useQuery(dockerNetworksQuery());
  const data = query.data;
  const [removing, setRemoving] = useState<RemoveSubject | null>(null);
  const { searchBox, owner } = useListFilters({ search, onSearchChange }, t("docker.networks.searchLabel"));
  const rows = useMemo(() => (data?.items === undefined ? undefined : visibleNetworks(data.items, search)), [data, search]);
  const columns: Column<DockerNetwork>[] = [
    {
      id: "name",
      header: t("docker.columns.network"),
      card: "title",
      sortValue: (row) => row.name,
      cell: (row) => (
        <span className="flex min-w-0 items-baseline gap-2">
          <Mono truncate>{row.name}</Mono>
          {row.builtin ? <Badge>{t("docker.networks.builtin")}</Badge> : null}
        </span>
      ),
    },
    { id: "driver", header: t("docker.columns.driver"), card: "meta", width: "w-32", hideBelow: "sm", cell: (row) => <Mono>{row.driver}</Mono> },
    { id: "owner", header: t("docker.columns.owner"), card: "meta", cell: (row) => <OwnerCell owner={row.owner} /> },
    {
      id: "containers",
      header: t("docker.columns.onIt"),
      card: "meta",
      hideBelow: "md",
      sortValue: (row) => row.containers?.length ?? 0,
      cell: (row) => ((row.containers?.length ?? 0) === 0 ? t("docker.columns.nobody") : <Mono truncate>{(row.containers ?? []).join(", ")}</Mono>),
    },
  ];
  const total = data?.items?.length ?? 0;
  return (
    <NodeCapabilityGate capability={DOCKER_CAPABILITY}>
      <ListPage
        header={{ title: t("docker.networks.title"), description: t("docker.networks.description") }}
        filters={<FilterBar label={t("docker.networks.filterLabel")} search={searchBox} filters={owner} count={rows === undefined ? undefined : t("docker.networks.count", { shown: rows.length, total })} />}
      >
        {query.isError && data === undefined ? (
          <ErrorBlock error={query.error} title={t("docker.networks.failed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
        ) : data !== undefined && !data.available ? (
          <Unavailable engine={data.engine} />
        ) : (
          <DataTable
            caption={t("docker.networks.caption")}
            columns={columns}
            rows={rows}
            getRowId={(row) => row.id}
            skeletonRows={6}
            rowActions={(row) => <NetworkMenu network={row} onRemove={setRemoving} />}
            empty={total === 0 ? <EmptyState variant="firstUse" icon={<OBJECT_ICONS.stackNetworks aria-hidden="true" />} title={t("docker.networks.empty.title")} description={t("docker.networks.empty.description")} /> : <NothingMatched onClear={() => onSearchChange({})} />}
          />
        )}
      </ListPage>
      <RemoveDialog subject={removing} onClose={() => setRemoving(null)} />
    </NodeCapabilityGate>
  );
}
