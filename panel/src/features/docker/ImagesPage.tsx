/**
 * Server > Docker > Images: every image Docker holds, whose it is, which containers use it and where it stands against its
 * registry. What an application keeps to go back to is never offered as unused, and what has a new version says so in the row. What
 * the last look at the registries found is shown with when it was; looking is a button, never something the page does by itself.
 */

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { dockerImagesQuery, dockerKeys } from "../../api/queries/docker";
import type { DockerImage } from "../../api/queries/docker";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { ErrorBlock } from "../../components/page/QueryState";
import { VersionMark } from "../../components/page/VersionMark";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { Mono } from "../../components/ui/Mono";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { NodeCapabilityGate } from "../../nodes/capability";
import { useJobInHand } from "../jobs/useJobInHand";
import { BringDialog } from "./BringDialog";
import { ImageMenu } from "./menus";
import { imageName, versionKindOf } from "./model";
import { NothingMatched, ownerMatches, textMatches, useListFilters } from "./lists";
import type { DockerListProps } from "./lists";
import { OwnerCell } from "./pieces";
import { RemoveDialog } from "./RemoveDialog";
import { UpdateImageDialog } from "./UpdateImageDialog";
import { useLookForNewVersions } from "./useLookForNewVersions";
import type { RemoveSubject } from "./RemoveDialog";
import { DOCKER_CAPABILITY } from "./SummaryPage";
import { Unavailable } from "./Unavailable";

const ANY = "all";

/** Whether the last look found a new version of this image (a new major included, which is a decision and is shown as one). */
export function hasNewVersion(image: Pick<DockerImage, "way_back" | "dangling" | "update">): boolean {
  const kind = versionKindOf(image);
  return kind === "newer" || kind === "major";
}

export function visibleImages(items: readonly DockerImage[], search: DockerListProps["search"]): DockerImage[] {
  return items.filter((item) => textMatches(search.q, ...(item.tags ?? []), item.id) && ownerMatches(item.owner, search.owner) && (search.news !== true || hasNewVersion(item)));
}

export function ImagesPage({ search, onSearchChange }: DockerListProps) {
  const t = useT();
  const query = useQuery(dockerImagesQuery());
  const data = query.data;
  const [removing, setRemoving] = useState<RemoveSubject | null>(null);
  const [updating, setUpdating] = useState<DockerImage | null>(null);
  const [bringing, setBringing] = useState<string | null>(null);
  const queryClient = useQueryClient();
  const look = useLookForNewVersions();
  // An update is the application's update, a job: it is followed where it was asked, and the page reads again when it ends.
  const update = useJobInHand(() => {
    void queryClient.invalidateQueries({ queryKey: dockerKeys.all });
  });
  const { searchBox, owner } = useListFilters({ search, onSearchChange }, t("docker.images.searchLabel"));
  const rows = useMemo(() => (data?.items === undefined ? undefined : visibleImages(data.items, search)), [data, search]);
  const columns: Column<DockerImage>[] = [
    {
      id: "name",
      header: t("docker.columns.image"),
      card: "title",
      sortValue: (row) => imageName(row),
      cell: (row) => (
        <span className="flex min-w-0 items-baseline gap-2">
          <Mono truncate>{imageName(row)}</Mono>
          {(row.tags?.length ?? 0) > 1 ? <span className="shrink-0 text-12 text-fg-muted">{t("docker.images.moreNames", { count: (row.tags?.length ?? 1) - 1 })}</span> : null}
        </span>
      ),
    },
    { id: "version", header: t("docker.columns.version"), card: "status", width: "w-56", cell: (row) => <VersionMark kind={versionKindOf(row)} /> },
    { id: "owner", header: t("docker.columns.owner"), card: "meta", cell: (row) => <OwnerCell owner={row.owner} /> },
    {
      id: "usedBy",
      header: t("docker.columns.usedBy"),
      card: "meta",
      hideBelow: "lg",
      sortValue: (row) => row.used_by?.length ?? 0,
      cell: (row) => ((row.used_by?.length ?? 0) === 0 ? t("docker.columns.nobody") : t("docker.images.usedByCount", { count: row.used_by?.length ?? 0 })),
    },
    {
      id: "size",
      header: t("docker.columns.size"),
      align: "end",
      mono: true,
      width: "w-28",
      sortValue: (row) => row.size_bytes ?? 0,
      cell: (row) => (row.size_bytes === null || row.size_bytes === undefined ? "" : formatBytes(row.size_bytes, t.locale)),
    },
  ];
  const total = data?.items?.length ?? 0;
  return (
    <NodeCapabilityGate capability={DOCKER_CAPABILITY}>
      <ListPage
        header={{ title: t("docker.images.title"), description: t("docker.images.description"), secondaryActions: look.button }}
        {...(look.notice !== null || update.notice !== null ? { notice: look.notice ?? update.notice } : {})}
        filters={
          <FilterBar
            label={t("docker.images.filterLabel")}
            search={searchBox}
            filters={
              <>
                <Select
                  aria-label={t("docker.images.versionFilterLabel")}
                  options={[
                    { value: ANY, label: t("docker.images.allImages") },
                    { value: "news", label: t("docker.images.withNews") },
                  ]}
                  value={search.news === true ? "news" : ANY}
                  onValueChange={(value) => onSearchChange({ ...search, news: value === "news" ? true : undefined })}
                />
                {owner}
              </>
            }
            count={rows === undefined ? undefined : t("docker.images.count", { shown: rows.length, total })}
          />
        }
      >
        {query.isError && data === undefined ? (
          <ErrorBlock error={query.error} title={t("docker.images.failed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
        ) : data !== undefined && !data.available ? (
          <Unavailable engine={data.engine} />
        ) : (
          <DataTable
            caption={t("docker.images.caption")}
            columns={columns}
            rows={rows}
            getRowId={(row) => row.id}
            skeletonRows={8}
            pagination="docker-images"
            rowActions={(row) => <ImageMenu image={row} onRemove={setRemoving} onUpdate={setUpdating} onBring={setBringing} />}
            empty={total === 0 ? <EmptyState variant="firstUse" icon={<OBJECT_ICONS.image aria-hidden="true" />} title={t("docker.images.empty.title")} description={t("docker.images.empty.description")} /> : <NothingMatched onClear={() => onSearchChange({})} />}
          />
        )}
      </ListPage>
      <RemoveDialog subject={removing} onClose={() => setRemoving(null)} />
      <UpdateImageDialog
        image={updating}
        onClose={() => setUpdating(null)}
        onQueued={(accepted) => update.track(accepted, { running: t("docker.update.running"), succeeded: t("docker.update.succeeded"), failed: t("docker.update.jobFailed") })}
      />
      <BringDialog stack={bringing} onClose={() => setBringing(null)} />
    </NodeCapabilityGate>
  );
}
