/**
 * What a database's column assumes its engine can do before the engine's own answer arrives: a SQL
 * engine's capabilities, so the sections drawn then are the ones that are nearly always there and
 * nothing moves when the answer comes. The register says which section each capability shows
 * (`DATABASE_SCREENS`); the column is `app/frame/columnBodies.tsx`.
 */

import type { Capability } from "./engines";

export const ASSUMED_CAPABILITIES: readonly Capability[] = ["tables", "sql", "dump", "users", "profiles", "metrics"];
