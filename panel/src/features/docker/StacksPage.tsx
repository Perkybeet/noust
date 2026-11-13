/**
 * Server > Docker > Stacks: every Compose project that runs, in two tables that are two different things: the ones Noust looks after
 * (an application's, a recipe's, a database engine's), which are changed from their owner, and the ones nobody of Noust made, which
 * are seen and controlled here and are brought to Noust to be edited or updated with a net.
 */

import { useQuery } from "@tanstack/react-query";
import { useMemo, useState } from "react";

import { dockerStacksQuery } from "../../api/queries/docker";
import type { DockerStack } from "../../api/queries/docker";
import { FilterBar } from "../../components/page/FilterBar";
import { ListPage } from "../../components/page/ListPage";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { ErrorBlock } from "../../components/page/QueryState";
import { Sections } from "../../components/page/Section";
import { SurfaceSection } from "../../components/page/SurfaceSection";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyState } from "../../components/ui/EmptyState";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { NodeCapabilityGate } from "../../nodes/capability";
import { Can } from "../../components/page/permissions";
import { Button } from "../../components/ui/Button";
import { BringDialog } from "./BringDialog";
import { StackMenu } from "./menus";
import { NothingMatched, ownerMatches, textMatches, useListFilters } from "./lists";
import type { DockerListProps } from "./lists";
import { OwnerCell, StackLink, StackPill } from "./pieces";
import { DOCKER_CAPABILITY } from "./SummaryPage";
import { Unavailable } from "./Unavailable";

/** The stacks a list shows once the page's filters are applied. */
export function visibleStacks(items: readonly DockerStack[], search: DockerListProps["search"]): DockerStack[] {
  return items.filter((item) => textMatches(search.q, item.name, item.working_dir) && ownerMatches(item.owner, search.owner) && (search.project === undefined || item.name === search.project));
}

export function StacksPage({ search, onSearchChange }: DockerListProps) {
  const t = useT();
  const query = useQuery(dockerStacksQuery());
  const [bringing, setBringing] = useState<string | null>(null);
  const data = query.data;
  const { searchBox, owner } = useListFilters({ search, onSearchChange }, t("docker.stacks.searchLabel"));
  const rows = useMemo(() => (data?.items === undefined ? undefined : visibleStacks(data.items, search)), [data, search]);
  const columns: Column<DockerStack>[] = [
    { id: "name", header: t("docker.columns.stack"), card: "title", sortValue: (row) => row.name, cell: (row) => <StackLink name={row.name} /> },
    { id: "state", header: t("docker.columns.state"), card: "status", width: "w-40", cell: (row) => <StackPill state={row.state} /> },
    { mono: true, id: "count", header: t("docker.columns.containers"), align: "end", width: "w-32", hideBelow: "sm", cell: (row) => `${String(row.running)}/${String(row.total)}` },
    { id: "owner", header: t("docker.columns.owner"), card: "meta", cell: (row) => <OwnerCell owner={row.owner} /> },
  ];
  const table = (items: readonly DockerStack[], caption: string) => (
    <DataTable
      caption={caption}
      columns={columns}
      rows={items}
      getRowId={(row) => row.name}
      embedded
      rowActions={(row) => (
        <div className="flex items-center gap-2">
          {row.owner.kind === "nobody" ? (
            <Can permission="docker.manage">
              <Button size="sm" variant="secondary" onClick={() => setBringing(row.name)}>
                {t("docker.menu.bring")}
              </Button>
            </Can>
          ) : null}
          <StackMenu stack={row} />
        </div>
      )}
    />
  );
  const ours = rows?.filter((row) => row.owner.kind !== "nobody") ?? [];
  const theirs = rows?.filter((row) => row.owner.kind === "nobody") ?? [];
  const total = data?.items?.length ?? 0;
  return (
    <NodeCapabilityGate capability={DOCKER_CAPABILITY}>
      <ListPage
        header={{ title: t("docker.stacks.title"), description: t("docker.stacks.description") }}
        filters={
          <FilterBar
            label={t("docker.stacks.filterLabel")}
            search={searchBox}
            filters={owner}
            count={rows === undefined ? undefined : t("docker.stacks.count", { shown: rows.length, total })}
          />
        }
      >
        {query.isError && data === undefined ? (
          <ErrorBlock error={query.error} title={t("docker.stacks.failed")} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
        ) : data !== undefined && !data.available ? (
          <Unavailable engine={data.engine} />
        ) : rows === undefined ? (
          <LoadingRegion label={t("docker.stacks.loading")}>
            <Skeleton className="h-40 w-full" />
          </LoadingRegion>
        ) : total === 0 ? (
          <EmptyState variant="firstUse" icon={<OBJECT_ICONS.compose aria-hidden="true" />} title={t("docker.stacks.empty.title")} description={t("docker.stacks.empty.description")} />
        ) : rows.length === 0 ? (
          <NothingMatched onClear={() => onSearchChange({})} />
        ) : (
          <Sections surfaces>
            {ours.length > 0 ? (
              <SurfaceSection title={t("docker.stacks.ours.title")} icon={OBJECT_ICONS.app} description={t("docker.stacks.ours.description")} flush>
                {table(ours, t("docker.stacks.ours.caption"))}
              </SurfaceSection>
            ) : null}
            {theirs.length > 0 ? (
              <SurfaceSection title={t("docker.stacks.theirs.title")} icon={OBJECT_ICONS.compose} description={t("docker.stacks.theirs.description")} flush>
                {table(theirs, t("docker.stacks.theirs.caption"))}
              </SurfaceSection>
            ) : null}
          </Sections>
        )}
      </ListPage>
      <BringDialog stack={bringing} onClose={() => setBringing(null)} />
    </NodeCapabilityGate>
  );
}
