import type { TuningStatus } from "../../../api/queries/databaseInstances";

/** What the form holds: text, as typed. An empty optional field asks for the default. */
export interface TuningDraft {
  memory: string;
  cpus: string;
  swap: string;
  interval: string;
  timeout: string;
  retries: string;
}

export type TuningField = keyof TuningDraft;

/** The request body of the plan and of the change: a setting left out stays as it is. */
export interface TuningBody {
  memory_mb?: number;
  cpus?: number;
  swap_mb?: number;
  interval_s?: number;
  timeout_s?: number;
  retries?: number;
  clear?: string[];
}

/** What the form says: the body to send, the fields that cannot be read, and whether anything changes. */
export interface TuningReading {
  body: TuningBody;
  invalid: Partial<Record<TuningField, true>>;
  changed: boolean;
}

/** The form as it starts: what the instance is held to now. */
export function draftOf(status: TuningStatus): TuningDraft {
  const { limits, probe } = status;
  return {
    memory: String(limits.memory_mb),
    cpus: limits.cpus === null || limits.cpus === undefined ? "" : String(limits.cpus),
    swap: limits.swap_mb === null || limits.swap_mb === undefined ? "" : String(limits.swap_mb),
    interval: String(probe.interval_s),
    timeout: String(probe.timeout_s),
    retries: String(probe.retries),
  };
}

/** A whole number from `min`, or null when the text is anything else. */
function whole(text: string, min: number): number | null {
  return /^\d{1,9}$/.test(text.trim()) && Number(text.trim()) >= min ? Number(text.trim()) : null;
}

/** A positive number with at most two decimals, or null. */
function decimal(text: string): number | null {
  const value = text.trim().replace(",", ".");
  return /^\d{1,3}(?:\.\d{1,2})?$/.test(value) && Number(value) > 0 ? Number(value) : null;
}

/**
 * Read the form against what the instance is now: only what differs is sent, an emptied optional
 * field asks for its default, and a field that is not a number is reported, not guessed.
 */
export function readTuning(status: TuningStatus, draft: TuningDraft): TuningReading {
  const { limits, probe } = status;
  const body: TuningBody = {};
  const clear: string[] = [];
  const invalid: TuningReading["invalid"] = {};

  const memory = whole(draft.memory, 1);
  if (memory === null) invalid.memory = true;
  else if (memory !== limits.memory_mb) body.memory_mb = memory;

  if (draft.cpus.trim() === "") {
    if (limits.cpus !== null && limits.cpus !== undefined) clear.push("cpus");
  } else {
    const cpus = decimal(draft.cpus);
    if (cpus === null) invalid.cpus = true;
    else if (cpus !== limits.cpus) body.cpus = cpus;
  }

  if (draft.swap.trim() === "") {
    if (limits.swap_mb !== null && limits.swap_mb !== undefined) clear.push("swap");
  } else {
    const swap = whole(draft.swap, 0);
    if (swap === null) invalid.swap = true;
    else if (swap !== limits.swap_mb) body.swap_mb = swap;
  }

  for (const [field, key, now, bodyKey, min] of [
    ["interval", "interval", probe.interval_s, "interval_s", 1],
    ["timeout", "timeout", probe.timeout_s, "timeout_s", 1],
    ["retries", "retries", probe.retries, "retries", 1],
  ] as const) {
    if (draft[field].trim() === "") {
      clear.push(key);
      continue;
    }
    const value = whole(draft[field], min);
    if (value === null) invalid[field] = true;
    else if (value !== now) body[bodyKey] = value;
  }

  if (clear.length > 0) body.clear = clear;
  return { body, invalid, changed: Object.keys(body).length > 0 };
}
