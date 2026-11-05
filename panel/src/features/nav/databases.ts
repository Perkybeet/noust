import { area, hidden, strip, tab } from "../../app/navCore";

/**
 * Databases and the engines they run on: what the databases are, and where they run (an engine of the
 * system, or a container of Noust's, in a version of its own). The register is app/nav.ts; this is one
 * group of it.
 */
export const DATABASES_SCREENS = strip(
  area("databases", "/databases", "nav.databases.label", {
    scope: "both",
    group: "runs",
    shortcut: ["g", "b"],
    goTo: "nav.databases.goTo",
    keywords: "nav.databases.keywords",
    rootLabel: "dbServers.column.databases",
  }),
  (databases) => [tab(databases, "engines", "dbServers.title", { exact: true, keywords: "dbServers.keywords" })],
);

/**
 * A database: the sections its engine can answer, never decided by its name (features/databases/tabs.ts).
 * The column groups them by what each is for: what you look at (`use`), what you set up (`configure`) and
 * what is dangerous (`danger`, Delete alone). Redis has Keys where a SQL engine has Data and no Query: its
 * engine does not say `tables` or `sql`.
 */
export const DATABASE_SCREENS = strip(hidden("database", "/databases/$engine/$name", "databases.detailTabs.overview", "databases", { parent: "databases", exact: true, intent: "use" }), (database) => [
  tab(database, "data", "databases.detailTabs.data", { needs: ["tables"], intent: "use" }),
  tab(database, "keys", "databases.detailTabs.keys", { to: "/databases/$engine/$name/data", needs: ["keys"], intent: "use" }),
  tab(database, "query", "databases.detailTabs.query", { needs: ["sql"], intent: "use" }),
  tab(database, "metrics", "databases.detailTabs.metrics", { needs: ["metrics"], intent: "use" }),
  tab(database, "access", "databases.detailTabs.access", { needs: ["users", "profiles"], intent: "configure", keywords: "databases.detailTabs.accessKeywords" }),
  tab(database, "connect", "databases.detailTabs.connect", { intent: "configure" }),
  tab(database, "backups", "databases.detailTabs.backups", { needs: ["dump"], intent: "configure" }),
  tab(database, "settings", "databases.detailTabs.settings", { intent: "configure" }),
  tab(database, "delete", "common.words.delete", { intent: "danger" }),
]);
