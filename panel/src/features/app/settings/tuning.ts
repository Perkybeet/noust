/**
 * The tuning of an application's unit as a form: what each field accepts and how its value
 * reaches `PUT /api/apps/{domain}/tuning`.
 *
 * The bounds and the words mirror `noust.managers.unit_tuning`, which is the check that counts;
 * this one only saves a round trip. An empty field removes the option, as a missing key does in
 * the request, and the request carries the whole tuning: a part of the form sends what the other
 * part holds unchanged.
 *
 * `parseTuning` takes a trailing `locale`, defaulting to the active one: a test calls it
 * directly and reads English, a component passes `t.locale`.
 */

import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { translate } from "../../../i18n/translate";

/** The limits and the ulimits: the half of the form that lives with the application's limits. */
export type LimitsGroup = "limits";
/** What the process keeps and opens: the half that lives with the unit's security. */
export type PrivilegesGroup = "privileges";
export type TuningGroup = LimitsGroup | PrivilegesGroup;

/** The options as the API names them (a missing key is not set). */
export interface TuningOptions {
  memory_high_mb?: number | null;
  memory_swap_max_mb?: number | null;
  cpu_weight?: number | null;
  allowed_cpus?: string | null;
  io_weight?: number | null;
  limit_nofile?: number | null;
  limit_nproc?: number | null;
  limit_memlock_mb?: number | string | null;
  core_dumps?: boolean | null;
  capabilities?: string[];
  devices?: { path: string; mode: string }[];
}

/** The form's text, one string per field. */
export interface TuningDraft {
  memoryHigh: string;
  swapMax: string;
  cpuWeight: string;
  allowedCpus: string;
  ioWeight: string;
  nofile: string;
  nproc: string;
  memlock: string;
  core: "" | "allow" | "forbid";
  capabilities: string[];
  devices: string;
}

export type TuningErrors = Partial<Record<keyof TuningDraft, string>>;

/** The fields of each half. */
export const FIELDS: Readonly<Record<TuningGroup, readonly (keyof TuningDraft)[]>> = {
  limits: ["memoryHigh", "swapMax", "cpuWeight", "allowedCpus", "ioWeight", "nofile", "nproc", "memlock", "core"],
  privileges: ["capabilities", "devices"],
};

/** The request's field names, for a 422 whose `fields` name them. */
export const FIELD_OF: Readonly<Record<string, keyof TuningDraft>> = {
  memory_high_mb: "memoryHigh",
  memory_swap_max_mb: "swapMax",
  cpu_weight: "cpuWeight",
  allowed_cpus: "allowedCpus",
  io_weight: "ioWeight",
  limit_nofile: "nofile",
  limit_nproc: "nproc",
  limit_memlock_mb: "memlock",
  core_dumps: "core",
  capabilities: "capabilities",
  devices: "devices",
};

/** Where the options are bounded (MIN_MEMORY_MB and the kernel's ceilings). */
export const BOUNDS = {
  memoryHigh: { min: 64, max: 16_777_216 },
  swapMax: { min: 0, max: 16_777_216 },
  cpuWeight: { min: 1, max: 10_000 },
  ioWeight: { min: 1, max: 10_000 },
  nofile: { min: 64, max: 1_048_576 },
  nproc: { min: 16, max: 4_194_304 },
  memlock: { min: 0, max: 1_048_576 },
} as const;

type Counted = keyof typeof BOUNDS;

const WHOLE = /^\d+$/;
const CPU_SET = /^\d+(?:-\d+)?(?:,\d+(?:-\d+)?)*$/;
const DEVICE_NODE = /^\/dev\/[A-Za-z0-9][A-Za-z0-9._/-]{0,127}$/;
const DEVICE_GROUP = /^(?:char|block)-[a-z0-9][a-z0-9_-]{0,63}$/;

export interface DeviceEntry {
  path: string;
  mode: string;
}

const text = (value: number | string | null | undefined): string => (value === null || value === undefined ? "" : String(value));

/** One device per line: `path` or `path:mode`. */
export function devicesOf(draft: string): DeviceEntry[] {
  return draft
    .split("\n")
    .map((line) => line.trim())
    .filter((line) => line !== "")
    .map((line) => {
      const colon = line.lastIndexOf(":");
      const mode = colon === -1 ? "" : line.slice(colon + 1);
      // A mode is r, w or rw; anything else after a colon is part of the (invalid) name.
      return mode === "r" || mode === "w" || mode === "rw" ? { path: line.slice(0, colon), mode } : { path: line, mode: "rw" };
    });
}

/** The form's starting text for the tuning an application has now. */
export function draftOf(options: TuningOptions | undefined): TuningDraft {
  const known = options ?? {};
  return {
    memoryHigh: text(known.memory_high_mb),
    swapMax: text(known.memory_swap_max_mb),
    cpuWeight: text(known.cpu_weight),
    allowedCpus: text(known.allowed_cpus),
    ioWeight: text(known.io_weight),
    nofile: text(known.limit_nofile),
    nproc: text(known.limit_nproc),
    memlock: text(known.limit_memlock_mb),
    core: known.core_dumps === true ? "allow" : known.core_dumps === false ? "forbid" : "",
    capabilities: [...(known.capabilities ?? [])],
    devices: (known.devices ?? []).map((device) => `${device.path}:${device.mode}`).join("\n"),
  };
}

/** How many fields of a half differ from what is stored, compared as typed (trimmed). */
export function changedIn(group: TuningGroup, draft: TuningDraft, stored: TuningDraft): number {
  return FIELDS[group].filter((field) => {
    if (field === "capabilities") return [...draft.capabilities].sort().join(" ") !== [...stored.capabilities].sort().join(" ");
    if (field === "devices") return JSON.stringify(devicesOf(draft.devices)) !== JSON.stringify(devicesOf(stored.devices));
    return (draft[field]).trim() !== (stored[field]).trim();
  }).length;
}

function counted(value: string): number | null | undefined {
  const trimmed = value.trim();
  if (trimmed === "") return null;
  return WHOLE.test(trimmed) ? Number.parseInt(trimmed, 10) : undefined;
}

export interface ParsedTuning {
  /** What the half sets: only the keys of its own fields. */
  values: TuningOptions;
  errors: TuningErrors;
}

/**
 * Reads one half of the form, with the backend's own wording for a value out of range.
 *
 * @param cores CPUs of the machine, when known; `allowed_cpus` names CPUs from 0.
 */
export function parseTuning(group: TuningGroup, draft: TuningDraft, cores: number | null, locale: Locale = getLocale()): ParsedTuning {
  const errors: TuningErrors = {};
  const values: TuningOptions = {};
  const notANumber = translate(locale, "runtime.tuning.notANumber");

  const count = (field: Counted, key: keyof TuningOptions): void => {
    const parsed = counted(draft[field]);
    if (field === "memlock" && draft.memlock.trim().toLowerCase() === "infinity") {
      (values as Record<string, unknown>)[key] = "infinity";
      return;
    }
    if (parsed === undefined) {
      errors[field] = notANumber;
      return;
    }
    const { min, max } = BOUNDS[field];
    if (parsed !== null && (parsed < min || parsed > max)) {
      errors[field] = translate(locale, "runtime.tuning.outOfRange", { value: parsed, min, max });
      return;
    }
    (values as Record<string, unknown>)[key] = parsed;
  };

  if (group === "limits") {
    count("memoryHigh", "memory_high_mb");
    count("swapMax", "memory_swap_max_mb");
    count("cpuWeight", "cpu_weight");
    count("ioWeight", "io_weight");
    count("nofile", "limit_nofile");
    count("nproc", "limit_nproc");
    count("memlock", "limit_memlock_mb");
    const cpus = draft.allowedCpus.replace(/\s+/g, "");
    if (cpus === "") values.allowed_cpus = null;
    else if (!CPU_SET.test(cpus)) errors.allowedCpus = translate(locale, "runtime.tuning.cpusSyntax");
    else {
      const last = Math.max(...cpus.split(",").flatMap((part) => part.split("-").map((n) => Number.parseInt(n, 10))));
      const backwards = cpus.split(",").some((part) => {
        const [low, high] = part.split("-").map((n) => Number.parseInt(n, 10));
        return high !== undefined && low !== undefined && low > high;
      });
      if (backwards) errors.allowedCpus = translate(locale, "runtime.tuning.cpusSyntax");
      else if (cores !== null && last >= cores) errors.allowedCpus = translate(locale, "runtime.tuning.cpusMissing", { cpu: last, cores });
      else values.allowed_cpus = cpus;
    }
    values.core_dumps = draft.core === "allow" ? true : draft.core === "forbid" ? false : null;
  } else {
    values.capabilities = [...draft.capabilities];
    const devices = devicesOf(draft.devices);
    const bad = devices.find((device) => !(device.path.startsWith("/dev/") ? DEVICE_NODE.test(device.path) && !device.path.split("/").includes("..") : DEVICE_GROUP.test(device.path)));
    if (bad !== undefined) errors.devices = translate(locale, "runtime.tuning.deviceInvalid", { device: bad.path });
    values.devices = devices;
  }
  return { values, errors };
}

/**
 * The whole tuning to send: what the application has now with this half replaced by the form's.
 * A key whose field is empty is dropped, because the request means "all of it".
 */
export function requestOptions(stored: TuningOptions | undefined, group: TuningGroup, values: TuningOptions): TuningOptions {
  const own = new Set<string>(
    group === "limits"
      ? ["memory_high_mb", "memory_swap_max_mb", "cpu_weight", "allowed_cpus", "io_weight", "limit_nofile", "limit_nproc", "limit_memlock_mb", "core_dumps"]
      : ["capabilities", "devices"],
  );
  const merged: Record<string, unknown> = Object.fromEntries(Object.entries(stored ?? {}).filter(([key]) => !own.has(key)));
  for (const [key, value] of Object.entries(values)) {
    if (value === null || value === undefined) continue;
    if (Array.isArray(value) && value.length === 0) continue;
    merged[key] = value;
  }
  return merged;
}

/** Device groups a GPU needs, as the API lists them. */
export function presetLines(groups: readonly string[]): string {
  return groups.map((group) => `${group}:rw`).join("\n");
}

/** Adds lines to a textarea's text without repeating one it already has. */
export function withLines(current: string, lines: string): string {
  const known = new Set(current.split("\n").map((line) => line.trim()));
  const added = lines.split("\n").filter((line) => !known.has(line.trim()));
  return [...current.split("\n").filter((line) => line.trim() !== ""), ...added].join("\n");
}
