/**
 * Which engine a database runs on, and whether it answers: the one reading every page of a database asks before it
 * draws what only a running engine can say. It comes from the list of engines (which the list of databases and the
 * Engines page read too), not from the failure of a request to the database: a stopped engine is a state of the
 * machine the page says in words, never an error the page ends in.
 */

import type { DbServer } from "../../api/queries/databases";
import { useDatabaseServers } from "./servers/useDatabaseServers";

export type EngineLife = "running" | "stopped" | "changing" | "unknown";

/** Whether an engine of the list answers: a stopped one, one being made or moved, or one the list does not say anything of. */
export function lifeOf(server: Pick<DbServer, "running" | "state"> | undefined): EngineLife {
  if (server === undefined) return "unknown";
  if (server.running) return "running";
  return server.state === "stopped" ? "stopped" : "changing";
}

/**
 * Whether it is worth asking the engine anything: the list of engines has been read and says it runs (or does not know it, which
 * is a reason to ask). A stopped engine answers every question with an error, and a page that asked would end in one.
 */
export function answers(of: { life: EngineLife; reading: boolean }): boolean {
  return !of.reading && of.life !== "stopped" && of.life !== "changing";
}

export interface EngineOfDatabase {
  /** The engine's entry in the list, by the key the database names as its engine (`postgresql`, `postgresql@pg16`). */
  server: DbServer | undefined;
  life: EngineLife;
  /** The list is still being read: nothing is known yet, and nothing should be drawn as if it were. */
  reading: boolean;
}

/** The engine a database runs on and whether it answers. */
export function useEngineOf(key: string): EngineOfDatabase {
  const { servers, isError } = useDatabaseServers();
  const server = servers?.find((candidate) => candidate.key === key);
  // A list that could not be read is not one still being read: the page asks the engine and says what it answers.
  return { server, life: lifeOf(server), reading: servers === undefined && !isError };
}
