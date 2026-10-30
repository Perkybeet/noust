/** What the databases list keeps of the whole, by the search params (`./search.ts`) that live in the URL. */

import type { BackupPolicy, Database, DbServer } from "../../api/queries/databases";
import { databaseId, databaseProtection } from "./protection";
import { matchesEngine } from "./servers";
import type { DatabasesSearch } from "./search";

/** The databases a search keeps, in their original order. */
export function filterDatabases(
  databases: readonly Database[],
  search: DatabasesSearch,
  policies: ReadonlyMap<string, BackupPolicy>,
  servers: readonly Pick<DbServer, "key" | "flavour">[] = [],
): Database[] {
  const needle = search.q?.toLowerCase();
  return databases.filter((database) => {
    // The engine is a flavour (`mariadb`) or, in an address from before, the key of a server.
    if (!matchesEngine(search.engine, database.engine, servers)) return false;
    if (search.tag !== undefined && !(database.tags ?? []).includes(search.tag)) return false;
    if (search.backups === "attention") {
      const { protection } = databaseProtection(database, policies.get(databaseId(database.engine, database.name)));
      if (protection === "protected" || protection === "scheduled") return false;
    }
    if (needle !== undefined) {
      const haystack = `${database.name} ${database.engine} ${[...(database.apps ?? []), ...(database.detected_apps ?? [])].join(" ")} ${(database.tags ?? []).join(" ")}`.toLowerCase();
      if (!haystack.includes(needle)) return false;
    }
    return true;
  });
}
