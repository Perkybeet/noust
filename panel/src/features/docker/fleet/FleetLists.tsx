/**
 * Docker across every server (Servers > Docker, "All servers"): the same five lists as one server's, with the server as a column and a filter
 * (`FleetList`), whose each thing is in view, and, on the first line of the body, which servers it speaks of and which did not answer fresh
 * (`ScopeLine`). A row opens that server's own page for it (the one link that chooses the server, in its words). Nothing is changed from
 * here: an action is that server's, done on its page, so no verb is offered on a row of a list that mixes machines.
 */

import { useQuery } from "@tanstack/react-query";
import { useNavigate } from "@tanstack/react-router";
import { useMemo } from "react";

import { dockerViewQuery, rowsOf } from "../../../api/queries/dockerFleet";
import type { DockerViewKind, FleetContainer, FleetImage, FleetNetwork, FleetRow, FleetStack, FleetVolume } from "../../../api/queries/dockerFleet";
import { FleetList } from "../../../components/page/FleetList";
import { ListPage } from "../../../components/page/ListPage";
import { ErrorBlock } from "../../../components/page/QueryState";
import { ScopeLine } from "../../../components/page/ScopeLine";
import { VersionMark } from "../../../components/page/VersionMark";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import type { T } from "../../../i18n";
import { formatBytes } from "../../../lib/format";
import { NothingMatched, ownerMatches, textMatches, useListFilters } from "../lists";
import type { DockerListProps } from "../lists";
import { imageName, versionKindOf } from "../model";
import { ContainerPill, OwnerCell, StackPill } from "../pieces";
import { stateMatches } from "../ContainersPage";
import { fleetServersOf } from "./servers";

/** The page of one thing on its own server: a container's, a stack's; the list for the rest. */
function pageOf(kind: DockerViewKind, name: string, row: FleetRow): { to: string; search: { node: string } } {
  const base = row.page !== "" ? row.page : `/server/docker/${kind}`;
  return { to: kind === "containers" || kind === "stacks" ? `${base}/${encodeURIComponent(name)}` : base, search: { node: row.node } };
}

interface Spec<R extends FleetRow> {
  kind: "stacks" | "containers" | "images" | "volumes" | "networks";
  /** The name of a row, which a row opens by. */
  nameOf: (row: R) => string;
  idOf: (row: R) => string;
  columns: (t: T) => Column<R>[];
  keep: (row: R, search: DockerListProps["search"]) => boolean;
}

const SPECS = {
  containers: {
    kind: "containers",
    nameOf: (row: FleetContainer) => row.name,
    idOf: (row: FleetContainer) => `${row.node}/${row.id}`,
    columns: (t: T): Column<FleetContainer>[] => [
      { id: "name", header: t("docker.columns.container"), card: "title", sortValue: (row) => row.name, cell: (row) => <Mono>{row.name}</Mono> },
      { id: "state", header: t("docker.columns.state"), card: "status", width: "w-44", cell: (row) => <ContainerPill kind={row.state_kind} /> },
      { id: "stack", header: t("docker.columns.stack"), card: "meta", hideBelow: "lg", cell: (row) => (row.project === "" ? t("docker.columns.noStack") : <Mono>{row.project}</Mono>) },
      { id: "owner", header: t("docker.columns.owner"), card: "meta", cell: (row) => <OwnerCell owner={row.owner} /> },
    ],
    keep: (row: FleetContainer, search: DockerListProps["search"]) =>
      textMatches(search.q, row.name, row.image, row.project) &&
      ownerMatches(row.owner, search.owner) &&
      stateMatches(row, search.state) &&
      (search.app === undefined || (row.owner.kind === "app" && row.owner.subject === search.app)),
  } satisfies Spec<FleetContainer>,
  stacks: {
    kind: "stacks",
    nameOf: (row: FleetStack) => row.name,
    idOf: (row: FleetStack) => `${row.node}/${row.name}`,
    columns: (t: T): Column<FleetStack>[] => [
      { id: "name", header: t("docker.columns.stack"), card: "title", sortValue: (row) => row.name, cell: (row) => <Mono>{row.name}</Mono> },
      { id: "state", header: t("docker.columns.state"), card: "status", width: "w-40", cell: (row) => <StackPill state={row.state} /> },
      { mono: true, id: "count", header: t("docker.columns.containers"), align: "end", width: "w-32", hideBelow: "sm", cell: (row) => `${String(row.running)}/${String(row.total)}` },
      { id: "owner", header: t("docker.columns.owner"), card: "meta", cell: (row) => <OwnerCell owner={row.owner} /> },
    ],
    keep: (row: FleetStack, search: DockerListProps["search"]) => textMatches(search.q, row.name, row.working_dir) && ownerMatches(row.owner, search.owner),
  } satisfies Spec<FleetStack>,
  images: {
    kind: "images",
    nameOf: (row: FleetImage) => imageName(row),
    idOf: (row: FleetImage) => `${row.node}/${row.id}`,
    columns: (t: T): Column<FleetImage>[] => [
      { id: "name", header: t("docker.columns.image"), card: "title", sortValue: (row) => imageName(row), cell: (row) => <Mono truncate>{imageName(row)}</Mono> },
      { id: "version", header: t("docker.columns.version"), card: "status", width: "w-56", cell: (row) => <VersionMark kind={versionKindOf(row)} /> },
      { id: "owner", header: t("docker.columns.owner"), card: "meta", cell: (row) => <OwnerCell owner={row.owner} /> },
      { mono: true, id: "size", header: t("docker.columns.size"), align: "end", width: "w-28", sortValue: (row) => row.size_bytes ?? 0, cell: (row) => (row.size_bytes === null || row.size_bytes === undefined ? "" : formatBytes(row.size_bytes, t.locale)) },
    ],
    keep: (row: FleetImage, search: DockerListProps["search"]) => {
      const kind = versionKindOf(row);
      return textMatches(search.q, ...(row.tags ?? []), row.id) && ownerMatches(row.owner, search.owner) && (search.news !== true || kind === "newer" || kind === "major");
    },
  } satisfies Spec<FleetImage>,
  volumes: {
    kind: "volumes",
    nameOf: (row: FleetVolume) => row.name,
    idOf: (row: FleetVolume) => `${row.node}/${row.name}`,
    columns: (t: T): Column<FleetVolume>[] => [
      { id: "name", header: t("docker.columns.volume"), card: "title", sortValue: (row) => row.name, cell: (row) => <Mono truncate>{row.anonymous ? row.name.slice(0, 12) : row.name}</Mono> },
      { id: "owner", header: t("docker.columns.owner"), card: "meta", cell: (row) => <OwnerCell owner={row.owner} /> },
      { id: "usedBy", header: t("docker.columns.usedBy"), card: "meta", hideBelow: "md", cell: (row) => ((row.used_by?.length ?? 0) === 0 ? t("docker.columns.nobody") : <Mono truncate>{(row.used_by ?? []).join(", ")}</Mono>) },
    ],
    keep: (row: FleetVolume, search: DockerListProps["search"]) => textMatches(search.q, row.name, row.project) && ownerMatches(row.owner, search.owner),
  } satisfies Spec<FleetVolume>,
  networks: {
    kind: "networks",
    nameOf: (row: FleetNetwork) => row.name,
    idOf: (row: FleetNetwork) => `${row.node}/${row.id}`,
    columns: (t: T): Column<FleetNetwork>[] => [
      { id: "name", header: t("docker.columns.network"), card: "title", sortValue: (row) => row.name, cell: (row) => <Mono truncate>{row.name}</Mono> },
      { id: "driver", header: t("docker.columns.driver"), card: "meta", width: "w-32", hideBelow: "sm", cell: (row) => <Mono>{row.driver}</Mono> },
      { id: "owner", header: t("docker.columns.owner"), card: "meta", cell: (row) => <OwnerCell owner={row.owner} /> },
    ],
    keep: (row: FleetNetwork, search: DockerListProps["search"]) => textMatches(search.q, row.name, row.driver, row.project) && ownerMatches(row.owner, search.owner),
  } satisfies Spec<FleetNetwork>,
};

/** One list of the fleet's Docker: `kind` says which. */
function FleetDockerList<R extends FleetRow>({ spec, search, onSearchChange }: DockerListProps & { spec: Spec<R> }) {
  const t = useT();
  const navigate = useNavigate();
  const query = useQuery(dockerViewQuery(spec.kind));
  const view = query.data;
  const all = rowsOf<R>(view);
  const servers = useMemo(() => fleetServersOf(view), [view]);
  const { searchBox, owner } = useListFilters({ search, onSearchChange }, t(`docker.fleet.${spec.kind}.searchLabel`));
  const rows = useMemo(() => all?.filter((row) => spec.keep(row, search)), [all, spec, search]);
  const columns = useMemo(() => spec.columns(t), [spec, t]);
  const total = all?.length ?? 0;
  return (
    <ListPage
      header={{ title: t(`docker.${spec.kind}.title`), description: t(`docker.fleet.${spec.kind}.description`) }}
      filters={
        <div className="flex min-w-0 flex-col gap-3">
          <ScopeLine scope={{ kind: "all", servers }} serverTarget={(server) => ({ to: `/server/docker/${spec.kind}`, search: { node: server.name } })} />
        </div>
      }
    >
      {query.isError && view === undefined ? (
        <ErrorBlock error={query.error} title={t(`docker.${spec.kind}.failed`)} onRetry={() => void query.refetch()} retrying={query.isRefetching} />
      ) : (
        <FleetList<R>
          scope="all"
          servers={servers}
          rows={rows}
          serverOf={(row) => row.node}
          columns={columns}
          getRowId={spec.idOf}
          caption={t(`docker.fleet.${spec.kind}.caption`)}
          filterLabel={t(`docker.fleet.${spec.kind}.filterLabel`)}
          search={searchBox}
          filters={owner}
          server={search.server ?? null}
          onServerChange={(server) => onSearchChange({ ...search, server: server ?? undefined })}
          count={(shown, of) => t("docker.fleet.count", { shown, total: of })}
          skeletonRows={8}
          pagination={`docker-fleet-${spec.kind}`}
          onRowActivate={(row) => {
            const target = pageOf(spec.kind, spec.nameOf(row), row);
            void navigate({ to: target.to as never, search: target.search as never });
          }}
          empty={total === 0 ? <EmptyState variant="inline" title={t(`docker.fleet.${spec.kind}.empty`)} /> : <NothingMatched onClear={() => onSearchChange({})} />}
        />
      )}
    </ListPage>
  );
}

export const FleetContainers = (props: DockerListProps) => <FleetDockerList spec={SPECS.containers} {...props} />;
export const FleetStacks = (props: DockerListProps) => <FleetDockerList spec={SPECS.stacks} {...props} />;
export const FleetImages = (props: DockerListProps) => <FleetDockerList spec={SPECS.images} {...props} />;
export const FleetVolumes = (props: DockerListProps) => <FleetDockerList spec={SPECS.volumes} {...props} />;
export const FleetNetworks = (props: DockerListProps) => <FleetDockerList spec={SPECS.networks} {...props} />;

