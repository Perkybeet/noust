/**
 * The database servers as the console draws them: what a database runs on, named for a person
 * ("PostgreSQL 17"), where its version stands in its project's support, which one is the default of
 * its engine, and the engines the side column filters by. Everything here is pure, so what the
 * screen says of a server is tested without drawing one.
 *
 * A server older than 4.1 has no list of servers: `serversFromEngines` draws one from its engines, so
 * the same screen serves it with what it has (no default of its own, no other versions to offer).
 */

import type { Database, DbServer, Engine } from "../../api/queries/databases";
import type { LogoSlug } from "../../components/ui/LogoTile";
import type { T } from "../../i18n";
import { formatMonth } from "../../lib/format";
import { engineFamily, isContainer } from "./engines";

/** The engines, in the CLI's order; a flavour is grouped with the engine it speaks. */
const FAMILY_ORDER: readonly string[] = ["postgresql", "mysql", "redis", "mongodb", "clickhouse"];
/** Inside an engine: MySQL, then the flavours that speak it. */
const FLAVOUR_ORDER: readonly string[] = ["postgresql", "mysql", "mariadb", "redis", "valkey", "mongodb", "clickhouse"];

/** The flavours whose databases Noust can move to a newer major version (`POST /api/databases/instances/migrate`). */
const MOVABLE: ReadonlySet<string> = new Set(["postgresql", "mysql", "mariadb", "mongodb"]);

const DAY_MS = 86_400_000;

/**
 * The release a version belongs to, as people name it: PostgreSQL's major number alone (17.2 is 17), the
 * first two numbers of every other engine (8.0.39 is 8.0, 11.4.2 is 11.4). A tag that is not a number
 * (`latest`) stays as it is, and no version says nothing.
 */
export function releaseOf(flavour: string, version: string | null | undefined): string | null {
  const text = (version ?? "").trim();
  if (text === "") return null;
  const numbers = /^\d+(?:\.\d+)*/.exec(text)?.[0];
  if (numbers === undefined) return text;
  const parts = numbers.split(".");
  return flavour === "postgresql" ? (parts[0] ?? text) : parts.slice(0, 2).join(".");
}

/** A server named for a person: its engine and release ("PostgreSQL 17"), the engine alone when it has no version. */
export function serverTitle(server: Pick<DbServer, "flavour" | "display_name" | "version">): string {
  const release = releaseOf(server.flavour, server.version);
  return release === null ? server.display_name : `${server.display_name} ${release}`;
}

/**
 * An engine named for a person with where it runs: "PostgreSQL 16 (on the system)", "PostgreSQL 15 (container pg15)",
 * "PostgreSQL 14 (the stack of shop.example.com)". The list of databases and the sentences about an engine say it so.
 */
export function serverWithPlace(t: T, server: Pick<DbServer, "flavour" | "display_name" | "version" | "runs_in" | "name" | "app" | "key">): string {
  const engine = serverTitle(server);
  const place = placeOf(server);
  if (place.kind === "noust") return t("dbServers.named.container", { engine, name: place.name });
  if (place.kind === "app") return t("dbServers.named.app", { engine, app: place.app });
  return t("dbServers.named.system", { engine });
}

export type SupportKind = "ok" | "soon" | "ended" | "unknown";

export interface SupportView {
  kind: SupportKind;
  /** The day the support ends or ended (`YYYY-MM-DD`), when one is published. */
  date: string | null;
  /** Whole days from today to that day; 0 once it has passed. Null with no date. */
  daysLeft: number | null;
}

/** Whole days from today (UTC) to a day written `YYYY-MM-DD`, never negative. */
export function daysLeft(endOfLife: string, now: Date): number {
  const end = Date.parse(`${endOfLife.slice(0, 10)}T00:00:00Z`);
  if (Number.isNaN(end)) return 0;
  const today = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  return Math.max(0, Math.floor((end - today) / DAY_MS));
}

/**
 * Where a server's version stands in its project's support. The server's own word decides between
 * supported and ending soon, and the date decides when it has ended: the console's clock may be ahead
 * of the one the word was said on, and a date that has passed is the answer whatever it said.
 */
export function supportOf(server: Pick<DbServer, "support" | "end_of_life" | "runs_in">, now: Date): SupportView {
  const date = server.end_of_life ?? null;
  if (server.runs_in === "app_container" || date === null) {
    return { kind: server.support === "ended" ? "ended" : "unknown", date, daysLeft: date === null ? null : daysLeft(date, now) };
  }
  const left = daysLeft(date, now);
  const passed = Date.parse(`${date.slice(0, 10)}T00:00:00Z`) <= Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate());
  if (server.support === "ended" || passed) return { kind: "ended", date, daysLeft: left };
  if (server.support === "ending_soon") return { kind: "soon", date, daysLeft: left };
  if (server.support === "supported") return { kind: "ok", date, daysLeft: left };
  return { kind: "unknown", date, daysLeft: left };
}

export type Place = { kind: "system" } | { kind: "noust"; name: string } | { kind: "app"; app: string };

/** Where a server runs: the system's own, a container Noust makes and runs, or an application's stack. */
export function placeOf(server: Pick<DbServer, "runs_in" | "name" | "app" | "key">): Place {
  if (server.runs_in === "noust_container") return { kind: "noust", name: server.name ?? server.key };
  if (server.runs_in === "app_container") return { kind: "app", app: server.app ?? "" };
  return { kind: "system" };
}

function familyRank(server: Pick<DbServer, "engine">): number {
  const at = FAMILY_ORDER.indexOf(server.engine);
  return at === -1 ? FAMILY_ORDER.length : at;
}

function flavourRank(server: Pick<DbServer, "flavour">): number {
  const at = FLAVOUR_ORDER.indexOf(server.flavour);
  return at === -1 ? FLAVOUR_ORDER.length : at;
}

/** A version as numbers, so 9 comes before 17 and 8.0 before 8.4. */
function versionParts(version: string | null | undefined): number[] {
  return (/^\d+(?:\.\d+)*/.exec((version ?? "").trim())?.[0] ?? "").split(".").map((part) => Number.parseInt(part, 10) || 0);
}

function newerFirst(a: Pick<DbServer, "version">, b: Pick<DbServer, "version">): number {
  const x = versionParts(a.version);
  const y = versionParts(b.version);
  for (let index = 0; index < Math.max(x.length, y.length); index += 1) {
    const difference = (y[index] ?? 0) - (x[index] ?? 0);
    if (difference !== 0) return difference;
  }
  return 0;
}

/** By engine in the CLI's order, then flavour, the default of each first, then the newest version, then the key. */
export function sortServers(servers: readonly DbServer[]): DbServer[] {
  return [...servers].sort(
    (a, b) =>
      familyRank(a) - familyRank(b) ||
      flavourRank(a) - flavourRank(b) ||
      Number(b.default) - Number(a.default) ||
      newerFirst(a, b) ||
      a.key.localeCompare(b.key),
  );
}

export type DefaultMark = { kind: "default" } | { kind: "defaultFor"; flavour: string };

/**
 * What a server says of being the default of its engine. MySQL and MariaDB speak one engine and each has
 * its own default, so beside the two of them a bare "Default" would be read as two defaults of one thing:
 * there it says which flavour it is the default of. Anywhere else it is "Default".
 */
export function defaultMark(server: DbServer, all: readonly DbServer[]): DefaultMark | null {
  if (!server.default) return null;
  const flavours = new Set(all.filter((other) => other.engine === server.engine).map((other) => other.flavour));
  return flavours.size > 1 ? { kind: "defaultFor", flavour: server.display_name } : { kind: "default" };
}

export interface EngineGroup {
  flavour: string;
  /** The engine's name for people ("MariaDB"). */
  name: string;
  /** How many databases run on a server of this flavour. */
  databases: number;
}

/** The engines of the side column: one per flavour there is a server of, with the databases each holds. */
export function engineGroups(servers: readonly DbServer[], databases: readonly Pick<Database, "engine">[]): EngineGroup[] {
  const byKey = new Map(servers.map((server) => [server.key, server]));
  const groups = new Map<string, EngineGroup>();
  for (const server of sortServers(servers)) {
    if (!groups.has(server.flavour)) groups.set(server.flavour, { flavour: server.flavour, name: server.display_name, databases: 0 });
  }
  for (const database of databases) {
    const flavour = byKey.get(database.engine)?.flavour;
    const group = flavour === undefined ? undefined : groups.get(flavour);
    if (group !== undefined) group.databases += 1;
  }
  return [...groups.values()];
}

/**
 * Whether a database passes an engine filter. The filter is a flavour (`mariadb`) or, in an address from
 * before servers had their own page, the key of a server (`postgresql@old`).
 */
export function matchesEngine(filter: string | undefined, serverKey: string, servers: readonly Pick<DbServer, "key" | "flavour">[]): boolean {
  if (filter === undefined) return true;
  if (serverKey === filter) return true;
  return servers.find((server) => server.key === serverKey)?.flavour === filter;
}

/** Whether Noust can move this server's databases to a newer version: it runs, it is not an application's, and it is an engine that can. */
export function canMoveUp(server: Pick<DbServer, "flavour" | "running" | "runs_in">): boolean {
  return server.running && server.runs_in !== "app_container" && MOVABLE.has(server.flavour);
}

/** The packaged mark of an engine's flavour, when the console has one: the others show the database's own icon, bare. */
export function logoFor(flavour: string): LogoSlug | null {
  switch (flavour) {
    case "postgresql":
    case "mysql":
    case "mariadb":
    case "mongodb":
    case "redis":
      return flavour;
    default:
      return null;
  }
}

/**
 * What a server says of its support, in the console's own words: until when it has it, how long is
 * left when it is about to end, or since when it has none. A server whose project publishes no date
 * says nothing (there is no sentence of "unknown" to read).
 */
export function supportLine(t: T, view: SupportView): string | null {
  if (view.kind === "unknown") return null;
  const date = view.date === null ? null : formatMonth(new Date(`${view.date.slice(0, 10)}T12:00:00Z`), t.locale);
  if (view.kind === "ended") return date === null ? t("dbServers.support.endedNoDate") : t("dbServers.support.ended", { date });
  if (date === null) return null;
  if (view.kind === "ok") return t("dbServers.support.until", { date });
  const days = view.daysLeft ?? 0;
  if (days >= 28) return t("dbServers.support.soonMonths", { date, count: Math.max(1, Math.round(days / 30)) });
  if (days >= 7) return t("dbServers.support.soonWeeks", { date, count: Math.round(days / 7) });
  return t("dbServers.support.soonDays", { date, count: Math.max(1, days) });
}

/** What the older server's support word says, as the newer one says it. */
function supportWord(status: string | undefined): string {
  return status === "supported" || status === "ending_soon" || status === "ended" ? status : "unknown";
}

/**
 * The servers of a Noust that has engines and no list of servers (4.0): the engines that are installed
 * or run in a container, each as a server of its own. It has no default of its own to tell (a host
 * engine is the one there is) and nothing to offer beyond what the engines' own page did, so `actions`
 * is empty.
 */
export function serversFromEngines(engines: readonly Engine[]): DbServer[] {
  return engines
    .filter((engine) => engine.installed || isContainer(engine))
    .map((engine): DbServer => {
      const family = engineFamily(engine) ?? engine.name.split("@")[0] ?? engine.name;
      const flavour = family === "mysql" && /mariadb/i.test(engine.version ?? "") ? "mariadb" : family;
      const container = isContainer(engine);
      const instance = container && engine.managed != null && engine.managed !== "";
      return {
        key: engine.name,
        engine: family,
        flavour,
        display_name: flavour === "mariadb" ? "MariaDB" : family === "mysql" ? "MySQL" : engine.display_name,
        version: (instance ? engine.pinned_version : engine.version) ?? engine.version ?? null,
        runs_in: !container ? "system" : instance ? "noust_container" : "app_container",
        name: instance ? (engine.managed ?? null) : null,
        state: engine.running ? "running" : "stopped",
        running: engine.running,
        port: engine.port,
        memory_limit_mb: engine.memory_limit_mb ?? null,
        image: engine.image ?? null,
        digest: null,
        custom_image: false,
        app: engine.app ?? null,
        support: supportWord(engine.support?.status),
        end_of_life: engine.support?.end_of_life ?? null,
        created_at: null,
        databases: null,
        default: !container,
        default_source: null,
        actions: [],
      };
    });
}
