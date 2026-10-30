/**
 * What each application keeps between deployments (task 16: keep files that must survive): the
 * folders its releases share (`shared/`: uploads, a database file), the files and folders bound
 * into it, and the volumes of its Compose stack. A release is thrown away and rebuilt; what an
 * application writes at run time lives in one of these or it is gone with the release. Read from
 * each application's own data (`/volume-backups/targets` and `/mounts`) and edited there: this lists,
 * so a person who wonders where the uploads are finds them without opening ten applications.
 */

import { useQueries, useQuery } from "@tanstack/react-query";
import { Link } from "@tanstack/react-router";

import { appsQuery } from "../../../api/queries/apps";
import { mountsQuery } from "../../../api/queries/runtime";
import { volumeTargetsQuery } from "../../../api/queries/volumeBackups";
import { Card } from "../../../components/ui/Card";
import { DataTable } from "../../../components/ui/DataTable";
import type { Column } from "../../../components/ui/DataTable";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Mono } from "../../../components/ui/Mono";
import { SkeletonCard } from "../../../components/ui/Skeleton";
import { textLinkClassName } from "../../../components/ui/TextLink";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import { formatBytes } from "../../../lib/format";

export interface KeptRow {
  key: string;
  domain: string;
  /** What is kept, as the application names it: `uploads`, a volume, a file. */
  target: string;
  /** Where it is on the server. */
  source: string | null;
  size: number | null;
  exists: boolean;
}

/** The applications whose data is read: enough for a real server, bounded so a big one does not ask for hundreds. */
const MOST_APPS = 30;

/** Whether an application can be given mounts at all (the server refuses a stack and a static site). */
export function takesMounts(app: { app_type?: string | null }): boolean {
  return app.app_type !== "static" && app.app_type !== "docker-compose";
}

export function KeptFolders() {
  const t = useT();
  const apps = useQuery(appsQuery());
  const listed = (apps.data?.apps ?? []).slice(0, MOST_APPS);
  const domains = listed.map((app) => app.domain);
  // A stack mounts what it needs in its own compose file and a static site serves its files from disk: the
  // server answers 400 to asking them for mounts, so they are not asked.
  const mounts = useQueries({ queries: listed.filter(takesMounts).map((app) => mountsQuery(app.domain)) });
  const targets = useQueries({ queries: domains.map((domain) => volumeTargetsQuery(domain)) });
  const loading = apps.isPending || mounts.some((query) => query.isPending) || targets.some((query) => query.isPending);
  const rows: KeptRow[] = [
    ...targets.flatMap((query, index) =>
      (query.data ?? []).map((target) => ({
        key: `${domains[index] ?? ""}:t:${target.key}`,
        domain: domains[index] ?? "",
        target: target.path ?? target.key,
        source: target.source === "" ? null : target.source,
        size: null,
        exists: target.exists,
      })),
    ),
    ...mounts.flatMap((query) =>
      (query.data?.mounts ?? []).map((mount) => ({
        key: `${query.data?.domain ?? ""}:m:${mount.target}`,
        domain: query.data?.domain ?? "",
        target: mount.target,
        source: mount.source ?? null,
        size: typeof mount.size === "number" ? mount.size : null,
        exists: mount.exists,
      })),
    ),
  ];
  const columns: Column<KeptRow>[] = [
    {
      id: "app",
      header: t("server.kept.column.app"),
      card: "title",
      sortValue: (row) => row.domain,
      cell: (row) => (
        <Link to="/apps/$domain" params={{ domain: row.domain }} className={textLinkClassName("inline", "min-w-0", "row")}>
          <Mono truncate>{row.domain}</Mono>
        </Link>
      ),
    },
    { id: "folder", header: t("server.kept.column.folder"), cell: (row) => <Mono truncate>{row.target}</Mono> },
    { id: "where", header: t("server.kept.column.where"), hideBelow: "lg", cell: (row) => <Mono tone="muted" truncate>{row.source ?? "-"}</Mono> },
    {
      id: "size",
      header: t("server.kept.column.size"),
      align: "end",
      width: "w-28",
      mono: true,
      card: "meta",
      sortValue: (row) => row.size ?? 0,
      cell: (row) => (row.exists ? (row.size !== null ? formatBytes(row.size, t.locale) : "-") : t("server.kept.notYet")),
    },
  ];
  if (loading && rows.length === 0) return <SkeletonCard lines={3} />;
  return (
    <Card level={2} title={t("server.kept.title")} icon={OBJECT_ICONS.mounts} description={t("server.kept.description")} padding="none">
      <DataTable
        columns={columns}
        rows={rows}
        getRowId={(row) => row.key}
        caption={t("server.kept.caption")}
        density="compact"
        empty={<EmptyState variant="inline" title={t("server.kept.none")} />}
      />
    </Card>
  );
}
