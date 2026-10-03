import { Link, useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";

import type { BackupPolicy, Database, DatabaseBackup, Engine } from "../../api/queries/databases";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Badge } from "../../components/ui/Badge";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { Mono } from "../../components/ui/Mono";
import { StatusPill } from "../../components/ui/StatusPill";
import { SM_UP, useMediaQuery } from "../../components/ui/useMediaQuery";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { engineName, instancePlace, isContainer } from "./engines";
import { PROTECTION_RANK, databaseId, databaseProtection } from "./protection";
import { TEXT_LINK } from "./ui";
import { VerifiedMark } from "./VerifiedMark";

export interface DatabasesTableProps {
  databases: readonly Database[];
  engines: readonly Engine[] | undefined;
  policies: ReadonlyMap<string, BackupPolicy>;
  dumps: ReadonlyMap<string, DatabaseBackup>;
  caption: string;
  rowActions: (database: Database) => ReactNode;
  empty: ReactNode;
}

/** A size the list sorts by: the engine reports it as words ("46.0 MB"), read back to bytes. */
export function sizeBytes(size: string | null | undefined): number | null {
  if (!size) return null;
  const match = /^([\d.]+)\s*([KMGTP]?i?B|bytes?)$/i.exec(size.trim());
  if (match === null) return null;
  const unit = (match[2] ?? "B").toUpperCase().replace("I", "");
  const power = { B: 0, BYTE: 0, BYTES: 0, KB: 1, MB: 2, GB: 3, TB: 4, PB: 5 }[unit] ?? 0;
  return Number(match[1]) * 1024 ** power;
}

/** The engine's size, in the console's own units. */
export function sizeWords(size: string | null | undefined, t: T): string | null {
  const bytes = sizeBytes(size);
  return bytes === null ? (size ?? null) : formatBytes(bytes, t.locale);
}

/**
 * The applications using a database, the first two named and the rest counted: those Noust
 * recorded, each a link to its Database tab, then those whose environment names it without a
 * recorded link, marked "Detected" in words (recording one is in the row's menu).
 */
function UsedBy({ apps, detected, t }: { apps: readonly string[]; detected: readonly string[]; t: T }) {
  const all = [
    ...apps.map((domain) => ({ domain, detected: false })),
    ...detected.filter((domain) => !apps.includes(domain)).map((domain) => ({ domain, detected: true })),
  ];
  if (all.length === 0) return <EmptyCell reason={t("databases.table.noApp")} />;
  const shown = all.slice(0, 2);
  return (
    <span className="flex min-w-0 items-center gap-x-2">
      {shown.map((app) =>
        app.detected ? (
          <span key={app.domain} className="flex min-w-0 items-center gap-1.5">
            <Link to="/apps/$domain" params={{ domain: app.domain }} translate="no" className={`${TEXT_LINK} truncate`}>
              {app.domain}
            </Link>
            <Badge>{t("databases.table.detected")}</Badge>
          </span>
        ) : (
          <Link key={app.domain} to="/apps/$domain/database" params={{ domain: app.domain }} translate="no" className={`${TEXT_LINK} truncate`}>
            {app.domain}
          </Link>
        ),
      )}
      {all.length > shown.length ? <span className="text-fg-muted">{t("databases.table.moreApps", { count: all.length - shown.length })}</span> : null}
    </span>
  );
}

/**
 * Where a database lives: the server's own engine by its name and version, or an instance in a
 * container, said in words ("Container") with where it runs and the application it belongs to.
 */
function EngineCell({ database, engines, t }: { database: Database; engines: readonly Engine[] | undefined; t: T }) {
  const instance = engines?.find((engine) => engine.name === database.engine);
  const version = database.engine_version ?? instance?.version;
  const container = instance !== undefined && isContainer(instance);
  const name = (
    <span className="flex min-w-0 items-center gap-1.5">
      <span className="truncate">{engineName(database.engine, engines)}</span>
      {version ? <Mono tone="muted">{version}</Mono> : null}
      {container ? <Badge>{t("databases.table.container")}</Badge> : null}
    </span>
  );
  if (!container) return name;
  return (
    <span className="flex min-w-0 flex-col">
      {name}
      <span className="flex min-w-0 items-center gap-1.5 text-12 text-fg-muted">
        <Mono tone="muted" truncate>
          {instancePlace(instance)}
        </Mono>
        {instance.app ? (
          <>
            <span aria-hidden="true" className="text-fg-faint">
              ·
            </span>
            <Link to="/apps/$domain" params={{ domain: instance.app }} translate="no" className={`${TEXT_LINK} truncate`}>
              {instance.app}
            </Link>
          </>
        ) : null}
      </span>
    </span>
  );
}

/**
 * The databases of a server, identity first: the name (which opens it), whether it is backed
 * up, its engine, who uses it, its size and its newest dump with what checking it found. On a
 * phone each row is a card with its menu in view.
 */
export function DatabasesTable({ databases, engines, policies, dumps, caption, rowActions, empty }: DatabasesTableProps) {
  const t = useT();
  const navigate = useNavigate();
  const wide = useMediaQuery(SM_UP);
  const columns: Column<Database>[] = [
    {
      id: "name",
      header: t("databases.table.name"),
      card: "title",
      sortValue: (database) => database.name,
      cell: (database) => (
        <span className="flex min-w-0 items-center gap-2">
          <Mono tone="default">{database.name}</Mono>
          {!database.tracked && !database.missing ? <Badge>{t("databases.table.untracked")}</Badge> : null}
          {database.unverified ? <Badge>{t("databases.table.unverified")}</Badge> : null}
        </span>
      ),
    },
    {
      id: "backups",
      header: t("databases.table.backups"),
      width: "w-44",
      card: "status",
      sortValue: (database) => PROTECTION_RANK[databaseProtection(database, policies.get(databaseId(database.engine, database.name))).protection],
      cell: (database) => {
        const view = databaseProtection(database, policies.get(databaseId(database.engine, database.name)));
        return <StatusPill appearance="inline" size="sm" state={view.state} label={t(view.label)} />;
      },
    },
    {
      id: "engine",
      header: t("databases.table.engine"),
      width: "w-60",
      sortValue: (database) => database.engine,
      cell: (database) => <EngineCell database={database} engines={engines} t={t} />,
    },
    {
      id: "apps",
      header: t("databases.table.usedBy"),
      hideBelow: "md",
      sortValue: (database) => [...(database.apps ?? []), ...(database.detected_apps ?? [])].join(",") || null,
      cell: (database) => <UsedBy apps={database.apps ?? []} detected={database.detected_apps ?? []} t={t} />,
    },
    {
      id: "size",
      header: t("databases.table.size"),
      align: "end",
      mono: true,
      width: "w-24",
      sortValue: (database) => sizeBytes(database.size),
      cell: (database) => sizeWords(database.size, t) ?? <EmptyCell reason={t("databases.table.noSize")} />,
    },
    {
      id: "lastBackup",
      header: t("databases.table.lastBackup"),
      width: "w-48",
      hideBelow: "sm",
      sortValue: (database) => (database.last_backup ? Date.parse(database.last_backup) : null),
      cell: (database) => {
        const newest = dumps.get(databaseId(database.engine, database.name));
        if (!database.last_backup && newest === undefined) return <EmptyCell reason={t("databases.table.neverBackedUp")} />;
        return (
          <span className="flex items-center gap-2">
            <RelativeTime value={newest?.created ?? database.last_backup} />
            {newest !== undefined ? <VerifiedMark status={newest.verify_status} compact /> : null}
          </span>
        );
      },
    },
  ];

  // A phone draws each row as a card: its facts on one line, the empty ones left out rather
  // than drawn as a row of dashes.
  const cardColumns: Column<Database>[] = [
    ...columns.slice(0, 2),
    {
      id: "facts",
      header: t("databases.table.facts"),
      card: "meta",
      cell: (database) => {
        const size = sizeWords(database.size, t);
        const newest = dumps.get(databaseId(database.engine, database.name));
        const apps = database.apps ?? [];
        const instance = engines?.find((engine) => engine.name === database.engine);
        const facts: ReactNode[] = [
          engineName(database.engine, engines),
          ...(instance !== undefined && isContainer(instance)
            ? [
                <span className="inline-flex items-center gap-1.5">
                  <Badge>{t("databases.table.container")}</Badge>
                  <Mono tone="muted">{instancePlace(instance)}</Mono>
                </span>,
              ]
            : []),
          ...(apps.length > 0 ? [<span translate="no">{apps.join(", ")}</span>] : []),
          ...(size !== null ? [size] : []),
          ...(newest !== undefined ? [<RelativeTime value={newest.created} />] : []),
        ];
        return (
          <span className="inline-flex min-w-0 flex-wrap items-center gap-x-1.5">
            {facts.map((fact, index) => (
              <span key={index} className="inline-flex items-center gap-x-1.5">
                {index > 0 ? (
                  <span aria-hidden="true" className="text-fg-faint">
                    ·
                  </span>
                ) : null}
                {fact}
              </span>
            ))}
          </span>
        );
      },
    },
  ];

  return (
    <DataTable
      columns={wide ? columns : cardColumns}
      rows={databases}
      getRowId={(database) => databaseId(database.engine, database.name)}
      caption={caption}
      defaultSort={{ column: "name", direction: "ascending" }}
      onRowActivate={(database) => void navigate({ to: "/databases/$engine/$name", params: { engine: database.engine, name: database.name } })}
      rowActions={rowActions}
      empty={empty}
      mobile="cards"
    />
  );
}
