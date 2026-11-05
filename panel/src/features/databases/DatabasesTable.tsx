import { Link, useNavigate } from "@tanstack/react-router";
import type { ReactNode } from "react";

import type { BackupPolicy, Database, DatabaseBackup, DbServer, Engine } from "../../api/queries/databases";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Badge } from "../../components/ui/Badge";
import { DataTable } from "../../components/ui/DataTable";
import type { Column } from "../../components/ui/DataTable";
import { EmptyCell } from "../../components/ui/EmptyCell";
import { LogoMark } from "../../components/ui/LogoTile";
import { Mono } from "../../components/ui/Mono";
import { NameWithTags } from "../../components/ui/NameWithTags";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { StatusPill } from "../../components/ui/StatusPill";
import { SM_UP, useMediaQuery } from "../../components/ui/useMediaQuery";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { formatBytes } from "../../lib/format";
import { ownerColumns } from "../teams/ownership";
import type { Labels, Scope } from "../teams/organization";
import { AddToNoust } from "./AddToNoust";
import { engineName } from "./engines";
import { PROTECTION_RANK, coveringPolicy, databaseId, databaseProtection } from "./protection";
import type { Protection } from "./protection";
import { lifeOf } from "./engineOf";
import { logoFor, placeOf, releaseOf, serverWithPlace } from "./servers";
import { TEXT_LINK } from "./ui";
import { VerifiedMark } from "./VerifiedMark";

export interface DatabasesTableProps {
  databases: readonly Database[];
  /** The servers they run on, by the key a database names as its `engine`. */
  servers: readonly DbServer[] | undefined;
  /** The engines of an older node, which name a database's engine when it has no servers. */
  engines?: readonly Engine[] | undefined;
  policies: ReadonlyMap<string, BackupPolicy>;
  dumps: ReadonlyMap<string, DatabaseBackup>;
  caption: string;
  rowActions: (database: Database) => ReactNode;
  empty: ReactNode;
  /** The list is still being read: placeholder rows stand where the databases will be. */
  loading?: boolean;
  /** Says where each database lives, when more than one place is in view (teams, projects, environments). */
  owner?: { scope: Scope; labels: Labels };
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

/** Whether Noust holds a database apart from the engine that has it: "not added" is not a kind of database but a relation. */
export function isNotAdded(database: Database): boolean {
  const state = (database.noust_state as string | undefined) ?? (!database.tracked && !database.missing ? "not_added" : "added");
  return state === "not_added";
}

/**
 * The applications using a database, the first two named and the rest counted: those Noust
 * recorded, each a link to its Database tab, then those whose environment names it without a
 * recorded link, marked "Detected" in words (recording one is in the row's menu).
 */
function UsedBy({ apps, detected, stack, t }: { apps: readonly string[]; detected: readonly string[]; stack?: string | undefined; t: T }) {
  const all = [
    // A database in an application's own container belongs to that application whether or not a link was recorded.
    ...(stack !== undefined && stack !== "" && !apps.includes(stack) ? [{ domain: stack, kind: "stack" as const }] : []),
    ...apps.map((domain) => ({ domain, kind: "linked" as const })),
    ...detected.filter((domain) => !apps.includes(domain) && domain !== stack).map((domain) => ({ domain, kind: "detected" as const })),
  ];
  if (all.length === 0) return <EmptyCell reason={t("databases.table.noApp")} />;
  const shown = all.slice(0, 2);
  return (
    <span className="flex min-w-0 items-center gap-x-2">
      {shown.map((app) =>
        app.kind === "linked" ? (
          <Link key={app.domain} to="/apps/$domain/database" params={{ domain: app.domain }} translate="no" className={`${TEXT_LINK} truncate`}>
            {app.domain}
          </Link>
        ) : (
          <span key={app.domain} className="flex min-w-0 items-center gap-1.5">
            <Link to="/apps/$domain" params={{ domain: app.domain }} translate="no" className={`${TEXT_LINK} truncate`}>
              {app.domain}
            </Link>
            {app.kind === "detected" ? <Badge>{t("databases.table.detected")}</Badge> : null}
          </span>
        ),
      )}
      {all.length > shown.length ? <span className="text-fg-muted">{t("databases.table.moreApps", { count: all.length - shown.length })}</span> : null}
    </span>
  );
}

/** The application whose own container holds a server, when it is one. */
function stackOf(server: DbServer | undefined): string | undefined {
  const place = server === undefined ? undefined : placeOf(server);
  return place?.kind === "app" ? place.app : undefined;
}

/** The engine's mark, before the name of a database: it says what the database is faster than the word in the next column. */
function EngineMark({ database, server }: { database: Database; server: DbServer | undefined }) {
  const logo = logoFor(server?.flavour ?? database.engine.split("@")[0] ?? database.engine);
  return logo !== null ? <LogoMark logo={logo} className="size-icon-lg shrink-0" /> : <OBJECT_ICONS.database aria-hidden="true" className="size-icon-lg shrink-0 text-fg-muted" />;
}

/**
 * The engine a database runs on, as people say it: its name and release and where it runs ("PostgreSQL 16 (on the system)",
 * "PostgreSQL 15 (container pg15)"). A database whose engine the list does not know (an older node, an engine still being
 * read) says its engine and the version it was read with.
 */
function EngineCell({ database, server, engines }: { database: Database; server: DbServer | undefined; engines: readonly Engine[] | undefined }) {
  const t = useT();
  const flavour = server?.flavour ?? database.engine.split("@")[0] ?? database.engine;
  const release = server === undefined ? releaseOf(flavour, database.engine_version) : null;
  return <span className="min-w-0 text-fg">{server !== undefined ? serverWithPlace(t, server) : [engineName(database.engine, engines), release].filter(Boolean).join(" ")}</span>;
}

/**
 * Whether a database is there to be used: its engine runs and it answers, its engine is stopped, the engine no longer has it,
 * or what it holds could not be read this time. Said in words with their shape, and the same in the database's own page.
 */
function StateCell({ database, server }: { database: Database; server: DbServer | undefined }) {
  const t = useT();
  const life = lifeOf(server);
  if (life === "stopped") return <StatusPill state="stopped" size="sm" label={t("common.statusPill.stopped")} />;
  if (life === "changing") return <StatusPill state="deploying" size="sm" label={t("databases.state.changing")} />;
  if (database.missing) return <StatusPill state="failed" size="sm" label={t("databases.state.missing")} />;
  if (database.unverified) return <StatusPill state="unknown" size="sm" label={t("databases.table.unverified")} />;
  return <StatusPill state="running" size="sm" label={t("common.statusPill.running")} />;
}

/** The size in words: a bar beside it would be a height of the table's own, and a level that is not a state takes no colour. */
function SizeCell({ database, t }: { database: Database; t: T }) {
  const words = sizeWords(database.size, t);
  if (words === null) return <EmptyCell reason={t("databases.table.noSize")} />;
  return <span className="tabular-nums">{words}</span>;
}

/**
 * Whether a database is backed up, in one cell: when its newest dump was taken (and whether checking it found it
 * sound), and otherwise the state that needs a look, in words with its shape. A database Noust does not hold yet and
 * that no schedule covers has no backup to speak of: where it would be is the way to add it.
 */
function BackupCell({ database, protection, newest, t }: { database: Database; protection: Protection; newest: DatabaseBackup | undefined; t: T }) {
  if (protection === "unprotected" && isNotAdded(database) && (database.actions ?? []).includes("add_to_noust")) return <AddToNoust database={database} />;
  if (protection === "protected") {
    const at = newest?.created ?? database.last_backup;
    return at ? (
      <span className="flex items-center gap-2">
        <RelativeTime value={at} />
        {newest !== undefined ? <VerifiedMark status={newest.verify_status} compact /> : null}
      </span>
    ) : (
      <span className="text-fg-muted">{t("databases.table.copy.protected")}</span>
    );
  }
  const states: Record<Exclude<Protection, "protected">, { state: "failed" | "queued" | "stopped" | "warning"; label: string }> = {
    failed: { state: "failed", label: t("common.words.failedFeminine") },
    scheduled: { state: "queued", label: t("databases.table.copy.scheduled") },
    paused: { state: "stopped", label: t("databases.table.copy.paused") },
    unprotected: { state: "warning", label: t("databases.table.copy.unprotected") },
    missing: { state: "failed", label: t("databases.table.copy.missing") },
  };
  const shown = states[protection];
  return <StatusPill appearance="inline" size="sm" state={shown.state} label={shown.label} />;
}

/**
 * The databases of a server, identity first: the name (which opens it), the server it runs on, who uses
 * it, its size and whether it is backed up. On a phone each row is a card with its actions in view.
 */
export function DatabasesTable({ databases, servers, engines, policies, dumps, caption, rowActions, empty, loading = false, owner }: DatabasesTableProps) {
  const t = useT();
  const navigate = useNavigate();
  const wide = useMediaQuery(SM_UP);
  const byKey = new Map((servers ?? []).map((server) => [server.key, server]));
  const protectionOf = (database: Database): Protection => databaseProtection(database, coveringPolicy(policies, database.engine, database.name)).protection;
  const columns: Column<Database>[] = [
    {
      id: "name",
      header: t("databases.table.name"),
      // The name is what the row is about: the table gives way elsewhere before it cuts a name that runs to 24 letters.
      width: "min-w-52",
      card: "title",
      sortValue: (database) => database.name,
      // The tags follow the name on its line, the first ones and "+N" for the rest: every row is the one height whatever it carries.
      cell: (database) => (
        <span className="flex min-w-0 items-center gap-2.5">
          {wide ? <EngineMark database={database} server={byKey.get(database.engine)} /> : null}
          <NameWithTags name={database.name} mono tags={database.tags ?? []} />
        </span>
      ),
    },
    {
      id: "state",
      header: t("databases.table.state"),
      width: "w-36",
      card: "status",
      sortValue: (database) => lifeOf(byKey.get(database.engine)),
      cell: (database) => <StateCell database={database} server={byKey.get(database.engine)} />,
    },
    {
      id: "server",
      header: t("databases.table.server"),
      hideBelow: "lg",
      sortValue: (database) => database.engine,
      cell: (database) => <EngineCell database={database} server={byKey.get(database.engine)} engines={engines} />,
    },
    ...(owner !== undefined ? ownerColumns<Database>(t, owner.scope, owner.labels) : []),
    {
      id: "size",
      header: t("databases.table.size"),
      align: "end",
      mono: true,
      width: "w-28",
      hideBelow: "md",
      sortValue: (database) => sizeBytes(database.size),
      cell: (database) => <SizeCell database={database} t={t} />,
    },
    {
      id: "apps",
      header: t("databases.table.app"),
      hideBelow: "md",
      sortValue: (database) => [...(database.apps ?? []), ...(database.detected_apps ?? [])].join(",") || null,
      cell: (database) => <UsedBy apps={database.apps ?? []} detected={database.detected_apps ?? []} stack={stackOf(byKey.get(database.engine))} t={t} />,
    },
    {
      id: "backup",
      header: t("databases.table.backup"),
      width: "w-40",
      card: "meta",
      sortValue: (database) => PROTECTION_RANK[protectionOf(database)],
      cell: (database) => {
        const protection = protectionOf(database);
        return <BackupCell database={database} protection={protection} newest={dumps.get(databaseId(database.engine, database.name))} t={t} />;
      },
    },
  ];

  // A phone draws each row as a card with its name, its state and one line of facts under them, each on a line of its
  // own: the kit joins several columns of a card's meta line, and an engine, an application and a size do not join.
  const [first, second, ...rest] = columns.filter((column) => column.id !== "server" && column.id !== "size" && column.id !== "apps");
  const phone: Column<Database>[] =
    first === undefined || second === undefined
      ? columns
      : [
          first,
          second,
          ...rest,
          {
            id: "facts",
            header: t("databases.table.facts"),
            card: "meta",
            cell: (database) => (
              <span className="flex min-w-0 flex-col gap-0.5">
                <EngineCell database={database} server={byKey.get(database.engine)} engines={engines} />
                <UsedBy apps={database.apps ?? []} detected={database.detected_apps ?? []} stack={stackOf(byKey.get(database.engine))} t={t} />
                {database.size ? <Mono tone="muted">{sizeWords(database.size, t)}</Mono> : null}
              </span>
            ),
          },
        ];
  return (
    <DataTable
      columns={wide ? columns : phone}
      rows={databases}
      getRowId={(database) => databaseId(database.engine, database.name)}
      caption={caption}
      defaultSort={{ column: "name", direction: "ascending" }}
      onRowActivate={(database) => void navigate({ to: "/databases/$engine/$name", params: { engine: database.engine, name: database.name } })}
      rowActions={rowActions}
      empty={empty}
      loading={loading}
      pagination="databases"
    />
  );
}
