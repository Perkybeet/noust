/**
 * What the startup check can be told besides the path, the statuses and the time it gets
 * (checklist B-12): how it asks (an HTTP request, or a command run as the application), the
 * request's method, scheme, Host header and port, the command and the service of a stack it
 * runs in, and the timing: how often, how many times, and how long a failure does not count.
 *
 * The rules and the words mirror `noust.validators.health`, which are the checks that count;
 * these only save a round trip. An empty field is the default, exactly as a null is in the
 * request. Every function takes a trailing `locale`, defaulting to the active one, like
 * `healthCheck.ts`.
 */

import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { translate } from "../../../i18n/translate";
import type { ResponseOf } from "../../../api/client";

export type HealthDetails = ResponseOf<"/api/apps/{domain}/health", "get">;

export type HealthKind = "http" | "command";

export const HEALTH_METHODS = ["GET", "HEAD", "OPTIONS", "POST"] as const;
export const HEALTH_SCHEMES = ["http", "https"] as const;

export const INTERVAL_MIN = 1;
export const INTERVAL_MAX = 60;
export const RETRIES_MIN = 1;
export const RETRIES_MAX = 100;
export const START_PERIOD_MIN = 0;
export const START_PERIOD_MAX = 600;

/** The probe's own defaults, as `docs/releases.md` states them. */
export const EXTRAS_DEFAULTS = { method: "GET", scheme: "http", interval: 2, startPeriod: 0 } as const;

export interface HealthExtrasDraft {
  kind: HealthKind;
  method: string;
  scheme: string;
  host: string;
  port: string;
  command: string;
  service: string;
  interval: string;
  retries: string;
  startPeriod: string;
}

export interface HealthExtrasValues {
  kind: HealthKind;
  method: string | null;
  scheme: string | null;
  host: string | null;
  port: number | null;
  command: string | null;
  service: string | null;
  interval: number | null;
  retries: number | null;
  start_period: number | null;
}

export type HealthExtrasErrors = Partial<Record<keyof HealthExtrasDraft, string>>;

/** A check that is a plain request with nothing else set: what every application starts with. */
export const EXTRAS_EMPTY: HealthExtrasDraft = {
  kind: "http",
  method: "",
  scheme: "",
  host: "",
  port: "",
  command: "",
  service: "",
  interval: "",
  retries: "",
  startPeriod: "",
};

function text(value: string | null | undefined): string {
  return value ?? "";
}

function number(value: number | null | undefined): string {
  return value === null || value === undefined ? "" : String(value);
}

/** The form as the application has it: an unset setting is an empty field. */
export function extrasDraftOf(health: Partial<HealthDetails> | undefined): HealthExtrasDraft {
  if (health === undefined) return EXTRAS_EMPTY;
  return {
    kind: health.kind === "command" ? "command" : "http",
    method: text(health.method),
    scheme: text(health.scheme),
    host: text(health.host),
    port: number(health.port),
    command: text(health.command),
    service: text(health.service),
    interval: number(health.interval),
    retries: number(health.retries),
    startPeriod: number(health.start_period),
  };
}

export function sameExtras(a: HealthExtrasDraft, b: HealthExtrasDraft): boolean {
  return (Object.keys(a) as (keyof HealthExtrasDraft)[]).every((key) => a[key].trim() === b[key].trim());
}

/** How many fields differ from what is stored. */
export function extrasChanged(draft: HealthExtrasDraft, stored: HealthExtrasDraft): number {
  return (Object.keys(stored) as (keyof HealthExtrasDraft)[]).filter((key) => draft[key].trim() !== stored[key].trim()).length;
}

/** Whether anything beyond a plain request is set, so the options open already. */
export function hasExtras(draft: HealthExtrasDraft): boolean {
  return !sameExtras(draft, EXTRAS_EMPTY);
}

const HOST = /^[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?$/;
const COMMAND_MAX = 2048;

function whole(raw: string, min: number, max: number): number | null {
  if (!/^\d+$/.test(raw)) return null;
  const value = Number.parseInt(raw, 10);
  return value >= min && value <= max ? value : null;
}

/** Reads the extra fields, with the backend's own wording for what it would refuse. */
export function parseExtras(
  draft: HealthExtrasDraft,
  locale: Locale = getLocale(),
): { values: HealthExtrasValues; errors: HealthExtrasErrors } {
  const errors: HealthExtrasErrors = {};
  const method = draft.method.trim().toUpperCase();
  const scheme = draft.scheme.trim().toLowerCase();
  const host = draft.host.trim();
  const command = draft.command.trim();
  const service = draft.service.trim();
  const port = draft.port.trim();
  const interval = draft.interval.trim();
  const retries = draft.retries.trim();
  const startPeriod = draft.startPeriod.trim();

  if (method !== "" && !(HEALTH_METHODS as readonly string[]).includes(method)) {
    errors.method = translate(locale, "runtime.health.methodHow", { methods: HEALTH_METHODS.join(", ") });
  }
  if (scheme !== "" && !(HEALTH_SCHEMES as readonly string[]).includes(scheme)) {
    errors.scheme = translate(locale, "runtime.health.schemeHow");
  }
  if (host !== "" && (host.length > 253 || !HOST.test(host))) errors.host = translate(locale, "runtime.health.hostHow");
  let portNumber: number | null = null;
  if (port !== "") {
    portNumber = whole(port, 1, 65535);
    if (portNumber === null) errors.port = translate(locale, "runtime.health.portRange");
  }
  let intervalNumber: number | null = null;
  if (interval !== "") {
    intervalNumber = whole(interval, INTERVAL_MIN, INTERVAL_MAX);
    if (intervalNumber === null) errors.interval = translate(locale, "runtime.health.intervalRange", { min: INTERVAL_MIN, max: INTERVAL_MAX });
  }
  let retriesNumber: number | null = null;
  if (retries !== "") {
    retriesNumber = whole(retries, RETRIES_MIN, RETRIES_MAX);
    if (retriesNumber === null) errors.retries = translate(locale, "runtime.health.retriesRange", { min: RETRIES_MIN, max: RETRIES_MAX });
  }
  let startNumber: number | null = null;
  if (startPeriod !== "") {
    startNumber = whole(startPeriod, START_PERIOD_MIN, START_PERIOD_MAX);
    if (startNumber === null) errors.startPeriod = translate(locale, "runtime.health.startPeriodRange", { min: START_PERIOD_MIN, max: START_PERIOD_MAX });
  }
  if (draft.kind === "command") {
    if (command === "") errors.command = translate(locale, "runtime.health.commandRequired");
    else if (command.length > COMMAND_MAX) errors.command = translate(locale, "runtime.health.commandTooLong", { max: COMMAND_MAX });
  }
  // What a kind does not use is not sent: a command check has no method, and a request no command.
  const request = draft.kind === "http";
  return {
    values: {
      kind: draft.kind,
      method: request && method !== "" ? method : null,
      scheme: request && scheme !== "" ? scheme : null,
      host: request && host !== "" ? host : null,
      port: request ? portNumber : null,
      command: !request && command !== "" ? command : null,
      service: !request && service !== "" ? service : null,
      interval: intervalNumber,
      retries: retriesNumber,
      start_period: startNumber,
    },
    errors,
  };
}

const FIELD_OF: Readonly<Record<string, keyof HealthExtrasDraft>> = {
  kind: "kind",
  method: "method",
  scheme: "scheme",
  host: "host",
  port: "port",
  command: "command",
  service: "service",
  interval: "interval",
  retries: "retries",
  start_period: "startPeriod",
};

/** The extra field a refusal's `fields` names, or null for a name that is not one of them. */
export function extrasFieldOf(name: string): keyof HealthExtrasDraft | null {
  return FIELD_OF[name] ?? null;
}
