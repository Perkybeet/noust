/**
 * The sections of a database's page, from what its engine can do (its capabilities), never
 * from its name: a Redis slot has Keys where a SQL database has Data, and no Query tab when
 * the engine has no console of its own.
 */

import type { LinkTab } from "../../app/LinkTabs";
import { tabsOf } from "../../app/navCore";
import { DATABASE_SCREENS } from "../nav/databases";
import type { Capability } from "./engines";

/** What a SQL engine has: the tabs drawn before the engine's own answer arrives. */
const ASSUMED: readonly Capability[] = ["tables", "sql", "dump", "users", "profiles", "metrics"];

/** The tabs of one database, its engine and name filled in. The register says which an engine's capabilities show. */
export function databaseTabs(engine: string, name: string, capabilities: readonly string[] | undefined): LinkTab[] {
  return tabsOf(DATABASE_SCREENS, { capabilities: capabilities ?? ASSUMED }).map((tab) => ({ ...tab, params: { engine, name } }));
}
