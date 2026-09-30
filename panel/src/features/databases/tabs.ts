/**
 * The sections of a database's page, from what its engine can do (its capabilities), never
 * from its name: a Redis slot has Keys where a SQL database has Data, and no Query tab when
 * the engine has no console of its own.
 */

import type { LinkTab } from "../../app/LinkTabs";
import type { Capability } from "./engines";

interface DatabaseTab {
  tab: Omit<LinkTab, "params">;
  /** Shown when the engine has any of these; always shown without. */
  needs?: readonly Capability[];
}

const TABS: readonly DatabaseTab[] = [
  { tab: { label: "databases.detailTabs.overview", to: "/databases/$engine/$name", exact: true } },
  { tab: { label: "databases.detailTabs.data", to: "/databases/$engine/$name/data" }, needs: ["tables"] },
  { tab: { label: "databases.detailTabs.keys", to: "/databases/$engine/$name/data" }, needs: ["keys"] },
  { tab: { label: "databases.detailTabs.query", to: "/databases/$engine/$name/query" }, needs: ["sql"] },
  { tab: { label: "databases.detailTabs.backups", to: "/databases/$engine/$name/backups" }, needs: ["dump"] },
  { tab: { label: "databases.detailTabs.users", to: "/databases/$engine/$name/users" }, needs: ["users", "profiles"] },
  { tab: { label: "databases.detailTabs.connect", to: "/databases/$engine/$name/connect" } },
  { tab: { label: "databases.detailTabs.metrics", to: "/databases/$engine/$name/metrics" }, needs: ["metrics"] },
];

/** What a SQL engine has: the tabs drawn before the engine's own answer arrives. */
const ASSUMED: readonly Capability[] = ["tables", "sql", "dump", "users", "profiles", "metrics"];

/** The tabs of one database, its engine and name filled in. */
export function databaseTabs(engine: string, name: string, capabilities: readonly string[] | undefined): LinkTab[] {
  const has = capabilities ?? ASSUMED;
  return TABS.filter(({ needs }) => needs === undefined || needs.some((capability) => has.includes(capability))).map(({ tab }) => ({
    ...tab,
    params: { engine, name },
  }));
}
