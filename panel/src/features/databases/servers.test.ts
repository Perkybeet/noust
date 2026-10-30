import { afterAll, beforeAll, describe, expect, it } from "vitest";

import type { Database, DbServer, Engine } from "../../api/queries/databases";
import { setLocale } from "../../app/locale";
import { bindT } from "../../i18n/useT";
import {
  canMoveUp,
  daysLeft,
  defaultMark,
  engineGroups,
  logoFor,
  matchesEngine,
  placeOf,
  releaseOf,
  serverTitle,
  serversFromEngines,
  sortServers,
  supportLine,
  supportOf,
} from "./servers";
import type { SupportView } from "./servers";

function server(extra: Partial<DbServer> & Pick<DbServer, "key" | "flavour">): DbServer {
  return {
    engine: extra.flavour === "mariadb" ? "mysql" : extra.flavour,
    display_name: extra.flavour,
    version: null,
    runs_in: "system",
    state: "running",
    running: true,
    custom_image: false,
    default: false,
    ...extra,
  };
}

const NOW = new Date("2026-10-29T12:00:00Z");

describe("a version as people name it", () => {
  it("is PostgreSQL's major alone and the first two numbers of every other engine", () => {
    expect(releaseOf("postgresql", "17.2")).toBe("17");
    expect(releaseOf("postgresql", "16")).toBe("16");
    expect(releaseOf("mysql", "8.0.39")).toBe("8.0");
    expect(releaseOf("mariadb", "11.4.2")).toBe("11.4");
    expect(releaseOf("redis", "7.0.15")).toBe("7.0");
    expect(releaseOf("mongodb", "7")).toBe("7");
  });

  it("leaves a tag that is not a number as it is, and says nothing for no version", () => {
    expect(releaseOf("redis", "latest")).toBe("latest");
    expect(releaseOf("mysql", null)).toBeNull();
    expect(releaseOf("mysql", "")).toBeNull();
  });

  it("titles a server by its engine and release", () => {
    expect(serverTitle(server({ key: "postgresql", flavour: "postgresql", display_name: "PostgreSQL", version: "17.2" }))).toBe("PostgreSQL 17");
    expect(serverTitle(server({ key: "mysql", flavour: "mysql", display_name: "MySQL", version: "8.0.39" }))).toBe("MySQL 8.0");
    expect(serverTitle(server({ key: "redis", flavour: "redis", display_name: "Redis", version: null }))).toBe("Redis");
  });
});

describe("where its version stands", () => {
  it("is ok with the date when support is published and ahead", () => {
    expect(supportOf(server({ key: "postgresql", flavour: "postgresql", support: "supported", end_of_life: "2029-11-08" }), NOW)).toEqual({ kind: "ok", date: "2029-11-08", daysLeft: 1106 });
  });

  it("is soon when it ends within the announced window, and says how long is left", () => {
    const view = supportOf(server({ key: "postgresql@old", flavour: "postgresql", support: "ending_soon", end_of_life: "2026-11-29" }), NOW);
    expect(view.kind).toBe("soon");
    expect(view.daysLeft).toBe(31);
  });

  it("is ended once the date has passed, whatever the server says of it", () => {
    expect(supportOf(server({ key: "mysql", flavour: "mysql", support: "ended", end_of_life: "2026-04-30" }), NOW).kind).toBe("ended");
    expect(supportOf(server({ key: "mysql", flavour: "mysql", support: "supported", end_of_life: "2026-04-30" }), NOW).kind).toBe("ended");
  });

  it("is unknown with no date, with no word, or for an application's own container", () => {
    expect(supportOf(server({ key: "redis", flavour: "redis", support: "unknown", end_of_life: null }), NOW).kind).toBe("unknown");
    expect(supportOf(server({ key: "redis", flavour: "redis" }), NOW).kind).toBe("unknown");
    expect(supportOf(server({ key: "postgresql@shop.db", flavour: "postgresql", runs_in: "app_container", support: null }), NOW).kind).toBe("unknown");
  });

  it("counts whole days between two dates, never negative", () => {
    expect(daysLeft("2026-10-30", NOW)).toBe(1);
    expect(daysLeft("2026-10-29", NOW)).toBe(0);
    expect(daysLeft("2026-01-01", NOW)).toBe(0);
  });
});

describe("what a server says of its support", () => {
  const en = bindT("en");
  const es = bindT("es");
  const view = (kind: SupportView["kind"], date: string | null, days: number | null): SupportView => ({ kind, date, daysLeft: days });

  // Spanish is fetched on demand: it is whole before the texts are asked for, and English again after.
  beforeAll(() => setLocale("es"));
  afterAll(() => setLocale("en"));

  it("says until when it has support, in the language's own month", () => {
    expect(supportLine(en, view("ok", "2029-11-08", 1106))).toBe("Support until Nov 2029");
    expect(supportLine(es, view("ok", "2029-11-08", 1106))).toBe("Soporte hasta nov 2029");
  });

  it("says how long is left when it is about to end: months, then weeks, then days, with the agreement of each language", () => {
    expect(supportLine(en, view("soon", "2026-11-29", 31))).toBe("Support until Nov 2026, 1 month left");
    expect(supportLine(en, view("soon", "2027-01-20", 83))).toBe("Support until Jan 2027, 3 months left");
    expect(supportLine(en, view("soon", "2026-11-12", 14))).toBe("Support until Nov 2026, 2 weeks left");
    expect(supportLine(en, view("soon", "2026-10-30", 1))).toBe("Support until Oct 2026, 1 day left");
    expect(supportLine(es, view("soon", "2026-11-29", 31))).toBe("Soporte hasta nov 2026, queda 1 mes");
    expect(supportLine(es, view("soon", "2027-01-20", 83))).toBe("Soporte hasta ene 2027, quedan 3 meses");
  });

  it("says since when it has none, or only that it has none when no date is published", () => {
    expect(supportLine(en, view("ended", "2026-04-30", 0))).toBe("No support since Apr 2026");
    expect(supportLine(es, view("ended", "2026-04-30", 0))).toBe("Sin soporte desde abr 2026");
    expect(supportLine(en, view("ended", null, null))).toBe("No support");
  });

  it("says nothing when the project publishes nothing", () => {
    expect(supportLine(en, view("unknown", null, null))).toBeNull();
    expect(supportLine(en, view("ok", null, null))).toBeNull();
  });

  it("draws the engines the console has a mark for and none for the others", () => {
    expect(logoFor("mariadb")).toBe("mariadb");
    expect(logoFor("redis")).toBe("redis");
    expect(logoFor("valkey")).toBeNull();
  });
});

describe("where a server runs", () => {
  it("is the system, a container of Noust's, or the stack of an application", () => {
    expect(placeOf(server({ key: "postgresql", flavour: "postgresql" }))).toEqual({ kind: "system" });
    expect(placeOf(server({ key: "mysql@legacy", flavour: "mysql", runs_in: "noust_container", name: "legacy" }))).toEqual({ kind: "noust", name: "legacy" });
    expect(placeOf(server({ key: "postgresql@shop.db", flavour: "postgresql", runs_in: "app_container", app: "shop.example.com" }))).toEqual({ kind: "app", app: "shop.example.com" });
  });
});

describe("the order of the servers and what says default", () => {
  const redis = server({ key: "redis", flavour: "redis", display_name: "Redis", default: true });
  const maria = server({ key: "mysql@maria", flavour: "mariadb", display_name: "MariaDB", version: "11.4", runs_in: "noust_container", name: "maria", default: true });
  const old = server({ key: "postgresql@old", flavour: "postgresql", display_name: "PostgreSQL", version: "14", runs_in: "noust_container", name: "old" });
  const mysql = server({ key: "mysql", flavour: "mysql", display_name: "MySQL", version: "8.0.39", default: true });
  const postgres = server({ key: "postgresql", flavour: "postgresql", display_name: "PostgreSQL", version: "17.2", default: true });
  const all = [redis, maria, old, mysql, postgres];

  it("groups by engine in the CLI's order, the default of each first and then the newest", () => {
    expect(sortServers(all).map((item) => item.key)).toEqual(["postgresql", "postgresql@old", "mysql", "mysql@maria", "redis"]);
  });

  it("says Default when the engine has one flavour and Default for it when MySQL and MariaDB are both there", () => {
    expect(defaultMark(postgres, all)).toEqual({ kind: "default" });
    expect(defaultMark(old, all)).toBeNull();
    expect(defaultMark(mysql, all)).toEqual({ kind: "defaultFor", flavour: "MySQL" });
    expect(defaultMark(maria, all)).toEqual({ kind: "defaultFor", flavour: "MariaDB" });
  });

  it("says nothing for a server that is not the default", () => {
    expect(defaultMark(server({ key: "postgresql@x", flavour: "postgresql" }), all)).toBeNull();
  });
});

describe("the engines of the side column", () => {
  const servers = [
    server({ key: "postgresql", flavour: "postgresql", display_name: "PostgreSQL" }),
    server({ key: "postgresql@old", flavour: "postgresql", display_name: "PostgreSQL" }),
    server({ key: "mysql", flavour: "mysql", display_name: "MySQL" }),
    server({ key: "mysql@maria", flavour: "mariadb", display_name: "MariaDB" }),
    server({ key: "redis", flavour: "redis", display_name: "Redis" }),
  ];
  const database = (name: string, engine: string) => ({ name, engine }) as Database;
  const databases = [database("a", "postgresql"), database("b", "postgresql@old"), database("c", "mysql"), database("d", "mysql@maria"), database("e", "redis")];

  it("is one per flavour, named for people, in the CLI's order, with the databases each holds", () => {
    expect(engineGroups(servers, databases)).toEqual([
      { flavour: "postgresql", name: "PostgreSQL", databases: 2 },
      { flavour: "mysql", name: "MySQL", databases: 1 },
      { flavour: "mariadb", name: "MariaDB", databases: 1 },
      { flavour: "redis", name: "Redis", databases: 1 },
    ]);
  });

  it("leaves out an engine nothing has", () => {
    expect(engineGroups([], databases)).toEqual([]);
  });

  it("matches a database to a flavour through the server it runs on, and to a server's own key", () => {
    expect(matchesEngine("mariadb", "mysql@maria", servers)).toBe(true);
    expect(matchesEngine("mysql", "mysql@maria", servers)).toBe(false);
    expect(matchesEngine("postgresql", "postgresql@old", servers)).toBe(true);
    // An address from before: the server's key.
    expect(matchesEngine("postgresql@old", "postgresql@old", servers)).toBe(true);
    expect(matchesEngine("postgresql@old", "postgresql", servers)).toBe(false);
    expect(matchesEngine(undefined, "anything", servers)).toBe(true);
  });
});

describe("moving to a newer version", () => {
  it("is offered for a running server of an engine Noust can move, never for an application's container", () => {
    expect(canMoveUp(server({ key: "postgresql", flavour: "postgresql" }))).toBe(true);
    expect(canMoveUp(server({ key: "mysql", flavour: "mariadb" }))).toBe(true);
    expect(canMoveUp(server({ key: "mongodb", flavour: "mongodb" }))).toBe(true);
    expect(canMoveUp(server({ key: "redis", flavour: "redis" }))).toBe(false);
    expect(canMoveUp(server({ key: "postgresql", flavour: "postgresql", running: false, state: "stopped" }))).toBe(false);
    expect(canMoveUp(server({ key: "postgresql@shop.db", flavour: "postgresql", runs_in: "app_container" }))).toBe(false);
  });
});

describe("a 4.0 server, which has engines and no list of servers", () => {
  const engine = (extra: Partial<Engine> & Pick<Engine, "name">): Engine =>
    ({
      display_name: extra.name,
      installed: true,
      version: null,
      running: true,
      port: 1,
      service: "x",
      capabilities: [],
      support: null,
      warnings: [],
      stored_account: false,
      kind: "host",
      ...extra,
    });

  it("is drawn from its engines: the ones installed or running, with where each stands", () => {
    const servers = serversFromEngines([
      engine({ name: "postgresql", display_name: "PostgreSQL", version: "16.4", support: { family: "postgresql", version: "16.4", major: "16", end_of_life: "2028-11-09", status: "supported", message: "" } }),
      engine({ name: "mongodb", display_name: "MongoDB", installed: false, running: false }),
      engine({ name: "mysql", display_name: "MySQL/MariaDB", version: "10.11.6-MariaDB" }),
      engine({ name: "postgresql@shop.db", engine_type: "postgresql", display_name: "PostgreSQL", kind: "container", app: "shop.example.com", version: "16.4" }),
    ]);
    expect(servers.map((item) => [item.key, item.flavour, item.runs_in, item.default])).toEqual([
      ["postgresql", "postgresql", "system", true],
      ["mysql", "mariadb", "system", true],
      ["postgresql@shop.db", "postgresql", "app_container", false],
    ]);
    expect(servers[0]).toMatchObject({ support: "supported", end_of_life: "2028-11-09", state: "running", actions: [] });
    expect(servers[2]).toMatchObject({ app: "shop.example.com" });
  });
});
