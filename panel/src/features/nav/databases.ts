import { area, hidden, strip, tab } from "../../app/navCore";

/**
 * Databases and its database servers: what the databases are, and where they run (an engine of the
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
  (databases) => [tab(databases, "servers", "dbServers.title", { exact: true })],
);

/** A database: the tabs its engine can do, never decided by its name (features/databases/tabs.ts). */
export const DATABASE_SCREENS = strip(hidden("database", "/databases/$engine/$name", "databases.detailTabs.overview", "databases", { parent: "databases", exact: true }), (database) => [
  tab(database, "data", "databases.detailTabs.data", { needs: ["tables"] }),
  tab(database, "keys", "databases.detailTabs.keys", { to: "/databases/$engine/$name/data", needs: ["keys"] }),
  tab(database, "query", "databases.detailTabs.query", { needs: ["sql"] }),
  tab(database, "backups", "databases.detailTabs.backups", { needs: ["dump"] }),
  tab(database, "users", "databases.detailTabs.users", { needs: ["users", "profiles"] }),
  tab(database, "connect", "databases.detailTabs.connect"),
  tab(database, "metrics", "databases.detailTabs.metrics", { needs: ["metrics"] }),
]);
