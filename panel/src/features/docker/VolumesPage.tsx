/**
 * Server > Docker > Volumes: where the data of every container lives, whose it is, how much it holds and who uses it. Removing one
 * is the one thing in the zone that loses data for good, so it asks for the volume's name; and what belongs to an application is
 * removed with the application, which says what it takes. What each volume holds is asked apart from the list, because Docker is
 * slow to say it and the list should not wait for it.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { dockerVolumeSizesQuery, dockerVolumesQuery } from "../../api/queries/docker";
import type { DockerVolume } from "../../api/queries/docker";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { NodeCapabilityGate } from "../../nodes/capability";
import { VolumeMenu } from "./menus";
import { NothingMatched, ownerMatches, textMatches, useListFilters } from "./lists";
import type { DockerListProps } from "./lists";
import { OwnerCell } from "./pieces";
import { RemoveDialog } from "./RemoveDialog";
import type { RemoveSubject } from "./RemoveDialog";
import { DOCKER_CAPABILITY } from "./SummaryPage";
import { Unavailable } from "./Unavailable";

export function visibleVolumes(items: readonly DockerVolume[], search: DockerListProps["search"]): DockerVolume[] {
  return items.filter(
    (item) =>
      textMatches(search.q, item.name, item.project, item.key) &&
      ownerMatches(item.owner, search.owner) &&
      (search.app === undefined || (item.owner.kind === "app" && item.owner.subject === search.app)) &&
      (search.project === undefined || item.project === search.project),
  );
}

export function VolumesPage({ search, onSearchChange }: DockerListProps) {
  const t = useT();
  const query = useQuery(dockerVolumesQuery());
  const data = query.data;
  // Sizes come once the list is on screen: the list is the page, and a number that is late must not hold it back.
  const sizes = useQuery({ ...dockerVolumeSizesQuery(), enabled: data?.available === true });
  const [removing, setRemoving] = useState<RemoveSubject | null>(null);
  const { searchBox, owner } = useListFilters({ search, onSearchChange }, t("docker.volumes.searchLabel"));
  const rows = useMemo(() => (data?.items === undefined ? undefined : visibleVolumes(data.items, search)), [data, search]);
  const sizeOf = (volume: DockerVolume): number | null => volume.size_bytes ?? sizes.data?.sizes?.[volume.name] ?? null;
  const columns: Column<DockerVolume>[] = [
    {
      id: "name",
      header: t("docker.columns.volume"),
      card: "title",
      sortValue: (row) => row.name,
      cell: (row) => (
        <span className="flex min-w-0 items-baseline gap-2">
          <Mono truncate>{row.anonymous ? row.name.slice(0, 12) : row.name}</Mono>
          {row.anonymous ? <span className="shrink-0 text-12 text-fg-muted">{t("docker.volumes.anonymous")}</span> : null}
        </span>
      ),
    },
    { id: "owner", header: t("docker.columns.owner"), card: "meta", cell: (row) => <OwnerCell owner={row.owner} /> },
    {
      id: "usedBy",
      header: t("docker.columns.usedBy"),
      card: "meta",
      hideBelow: "md",
      cell: (row) => ((row.used_by?.length ?? 0) === 0 ? t("docker.columns.nobody") : <Mono truncate>{(row.used_by ?? []).join(", ")}</Mono>),
    },
    {
      id: "size",
      header: t("docker.columns.size"),
      align: "end",
      mono: true,
      width: "w-28",
      sortValue: (row) => sizeOf(row) ?? -1,
      cell: (row) => {
        const size = sizeOf(row);
        return size === null ? <EmptyCell reason={t("docker.volumes.sizeLoading")} /> : formatBytes(size, t.locale);
      },
    },
  ];
  const total = data?.items?.length ?? 0;
  return (
    <NodeCapabilityGate capability={DOCKER_CAPABILITY}>
      <ListPage
        header={{ title: t("docker.volumes.title"), description: t("docker.volumes.description") }}
        filters={<FilterBar label={t("docker.volumes.filterLabel")} search={searchBox} filters={owner} count={rows === undefined ? undefined : t("docker.volumes.count", { shown: rows.length, total })} />}
      >
        {query.isError && data === undefined ? (
          <ErrorBlock error={query.error} title={t("docker.volumes.failed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
        ) : data !== undefined && !data.available ? (
          <Unavailable engine={data.engine} />
        ) : (
          <DataTable
            caption={t("docker.volumes.caption")}
            columns={columns}
            rows={rows}
            getRowId={(row) => row.name}
            skeletonRows={6}
            pagination="docker-volumes"
            rowActions={(row) => <VolumeMenu volume={{ ...row, size_bytes: sizeOf(row) }} onRemove={setRemoving} />}
            empty={total === 0 ? <EmptyState variant="firstUse" icon={<OBJECT_ICONS.volume aria-hidden="true" />} title={t("docker.volumes.empty.title")} description={t("docker.volumes.empty.description")} /> : <NothingMatched onClear={() => onSearchChange({})} />}
          />
        )}
      </ListPage>
      <RemoveDialog subject={removing} onClose={() => setRemoving(null)} />
    </NodeCapabilityGate>
  );
}
