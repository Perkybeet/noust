/**
 * What the summary says about each thing that asks for the operator, read from the facts the server gives (`facts` is data, never a
 * sentence: the console words it). Pure functions, so each sentence's choice is a test.
 */

import type { DockerAttention } from "../../api/queries/docker";

type Facts = DockerAttention["facts"];

function number(facts: Facts, key: string): number | null {
  const value = facts?.[key];
  return typeof value === "number" ? value : null;
}

function text(facts: Facts, key: string): string | null {
  const value = facts?.[key];
  return typeof value === "string" && value !== "" ? value : null;
}

function flag(facts: Facts, key: string): boolean {
  return facts?.[key] === true;
}

/** How a container that keeps falling ended, as the sentence under it says it. */
export type LoopWhy =
  /** Memory ran out (137 with the engine's own word for it). */
  | { kind: "oom"; code: number | null; limitBytes: number | null; restarts: number; minutes: number }
  /** It was killed with 137 and memory was not the reason. */
  | { kind: "killed"; restarts: number; minutes: number }
  /** It ended with a failure of its own. */
  | { kind: "error"; code: number | null; restarts: number; minutes: number };

export function loopWhy(facts: Facts): LoopWhy {
  const code = number(facts, "exit_code");
  const restarts = number(facts, "restarts_recent") ?? 0;
  const minutes = number(facts, "window_minutes") ?? 10;
  if (flag(facts, "oom")) return { kind: "oom", code, limitBytes: number(facts, "memory_limit_bytes"), restarts, minutes };
  if (code === 137) return { kind: "killed", restarts, minutes };
  return { kind: "error", code, restarts, minutes };
}

/** Why a container that is not running asks for the operator, and whether its policy says it should run. */
export interface StoppedWhy {
  /** `terminated` (143, somebody stopped it), `ended` (0), `error`, `killed`, `oom`, `dead`, `stopped` and the rest of the closed list. */
  kind: string;
  code: number | null;
  /** Docker's restart policy: `no` means it does not come back by itself. */
  policy: string;
  finishedAt: string | null;
}

export function stoppedWhy(facts: Facts): StoppedWhy {
  return { kind: text(facts, "kind") ?? "stopped", code: number(facts, "exit_code"), policy: text(facts, "policy") ?? "no", finishedAt: text(facts, "finished_at") };
}

/** The references of the images with a new version (`update` or `major`) and when the registries were last asked. */
export function versionFacts(facts: Facts): { images: string[]; checkedAt: string | null } {
  const raw = facts?.["images"];
  return { images: Array.isArray(raw) ? raw.filter((entry): entry is string => typeof entry === "string") : [], checkedAt: text(facts, "checked_at") };
}

/** What is left of the disk Docker lives on, and what could be taken back without touching data. */
export function diskFacts(facts: Facts): { free: number | null; total: number | null; reclaimable: number | null } {
  return { free: number(facts, "free_bytes"), total: number(facts, "total_bytes"), reclaimable: number(facts, "reclaimable_bytes") };
}
