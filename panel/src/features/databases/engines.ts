/**
 * What the console knows about engines, from what each engine says about itself: its
 * capabilities decide the tabs a database page draws (never a test of the engine's name), and
 * where its version stands upstream decides the notice beside it.
 */

import type { Engine, SupportNotice } from "../../api/queries/databases";
import type { Status } from "../../components/ui/StatusPill";
import type { PlainKey, T } from "../../i18n";
import { formatDate, parseTimestamp } from "../../lib/format";

/** A capability `GET /api/databases/engines` reports (`noust.managers.database.base.CAPABILITIES`). */
export type Capability = "sql" | "tables" | "keys" | "documents" | "read_only" | "users" | "profiles" | "dump" | "metrics";

/** Whether an engine (as listed) can do something. */
export function can(engine: { capabilities?: readonly string[] | undefined } | undefined | null, capability: Capability): boolean {
  return (engine?.capabilities ?? []).includes(capability);
}

/** The engines, in the order the CLI lists them, and the names they are shown by when unknown. */
const DISPLAY_NAMES: Readonly<Record<string, string>> = {
  postgresql: "PostgreSQL",
  mysql: "MySQL/MariaDB",
  redis: "Redis",
  mongodb: "MongoDB",
};

export const ENGINE_ORDER: readonly string[] = ["postgresql", "mysql", "redis", "mongodb"];

/** An engine's name for people: the engine's own display name when it is known. */
export function engineName(engine: string, engines?: readonly Engine[]): string {
  return engines?.find((item) => item.name === engine)?.display_name ?? DISPLAY_NAMES[engine] ?? engine;
}

/**
 * Whether a listed engine is an instance in a container rather than the server's own engine:
 * the image decides its engine and version, so it is never installed, uninstalled or configured
 * from the console (the server refuses it too, and says why).
 */
export function isContainer(engine: { kind?: string | null | undefined } | undefined | null): boolean {
  return engine?.kind === "container";
}

/** Where a container instance runs: its Compose project and service, else the container's name. */
export function instancePlace(engine: Pick<Engine, "name" | "container" | "project" | "compose_service">): string {
  if (engine.project && engine.compose_service) return `${engine.project}/${engine.compose_service}`;
  return engine.container ?? engine.name;
}

/** An engine's name in a list of choices: an instance in a container also says where it runs. */
export function instanceLabel(t: T, engine: Engine): string {
  return isContainer(engine) ? t("databases.instances.choice", { engine: engine.display_name, place: instancePlace(engine) }) : engine.display_name;
}

/** The server's own engines first, in the CLI's order, then the containers by where they run. */
export function sortInstances(engines: readonly Engine[]): Engine[] {
  const hosts = sortEngines(engines.filter((engine) => !isContainer(engine)));
  const containers = engines.filter(isContainer).sort((a, b) => instancePlace(a).localeCompare(instancePlace(b)));
  return [...hosts, ...containers];
}

/** Engines in the CLI's order, anything the console does not know last. */
export function sortEngines<T extends { name: string }>(engines: readonly T[]): T[] {
  const rank = (name: string): number => {
    const at = ENGINE_ORDER.indexOf(name);
    return at === -1 ? ENGINE_ORDER.length : at;
  };
  return [...engines].sort((a, b) => rank(a.name) - rank(b.name) || a.name.localeCompare(b.name));
}

/** An engine's life on this machine, as a state of the console's vocabulary. */
export function engineState(engine: Pick<Engine, "installed" | "running">): { state: Status; label: PlainKey } {
  if (!engine.installed) return { state: "stopped", label: "databases.engine.state.notInstalled" };
  if (engine.running) return { state: "running", label: "databases.engine.state.running" };
  return { state: "stopped", label: "databases.engine.state.stopped" };
}

type SupportStatus = "ended" | "ending_soon" | "supported";

/**
 * Where a version stands upstream, when it is worth saying: an ended or ending version is a
 * warning (the distribution may still patch it, so never a failure); a supported one is a
 * fact, neutral.
 */
export function supportView(support: SupportNotice | null | undefined): { warn: boolean; status: SupportStatus } | null {
  if (!support) return null;
  switch (support.status) {
    case "ended":
      return { warn: true, status: "ended" };
    case "ending_soon":
      return { warn: true, status: "ending_soon" };
    case "supported":
      return support.end_of_life ? { warn: false, status: "supported" } : null;
    default:
      return null;
  }
}

/**
 * The words a slot of Redis is shown with, rather than a name: a Redis "database" is a numbered
 * slot of one instance.
 */
export function isSlotEngine(engine: { capabilities?: readonly string[] | undefined } | undefined | null): boolean {
  return can(engine, "keys") && !can(engine, "tables");
}

/** Where a version stands upstream, as one sentence of the console's own. */
export function supportText(t: T, support: SupportNotice): string | null {
  const view = supportView(support);
  if (view === null) return null;
  // A date alone ("2028-11-09") is a day, not a moment: read at noon so no zone moves it.
  const when = parseTimestamp(support.end_of_life && /^\d{4}-\d{2}-\d{2}$/.test(support.end_of_life) ? `${support.end_of_life} 12:00` : support.end_of_life);
  const date = when === null ? (support.end_of_life ?? "") : formatDate(when, {}, t.locale);
  const version = support.major ?? support.version ?? "";
  switch (view.status) {
    case "ended":
      return t("databases.engine.support.ended", { version, date });
    case "ending_soon":
      return t("databases.engine.support.endingSoon", { version, date });
    case "supported":
      return t("databases.engine.support.supported", { version, date });
  }
}
