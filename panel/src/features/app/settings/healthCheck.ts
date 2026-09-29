/**
 * The health check and retention forms: what each field accepts and how its value reaches
 * `PATCH /api/apps/{domain}/health` and `PATCH /api/apps/{domain}/releases/retention`.
 *
 * The rules and the words mirror `noust.validators.health` and the store's retention bounds,
 * which are the checks that count; these only save a round trip. An empty field is the
 * default, exactly as a null is in the request.
 *
 * Every function below takes a trailing `locale`, defaulting to the active one: a test calls
 * it directly and reads English, a component passes `t.locale`.
 */

import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { translate } from "../../../i18n/translate";
import type { App } from "../../../api/queries/apps";

/**
 * The gate's own defaults, as `docs/releases.md` states them. The accepted statuses ("any
 * status below 500") are words, so they are the catalog's: `appSettings.healthCheck.defaultExpect`.
 */
export const HEALTH_DEFAULTS = { path: "/", timeout: 30 } as const;

export const HEALTH_TIMEOUT_MIN = 5;
export const HEALTH_TIMEOUT_MAX = 600;
const PATH_MAX = 1024;

export const RETENTION_MIN = 1;
export const RETENTION_MAX = 50;

export interface HealthDraft {
  path: string;
  expect: string;
  timeout: string;
}

export interface HealthValues {
  path: string | null;
  expect: string | null;
  timeout: number | null;
}

export type HealthErrors = Partial<Record<keyof HealthDraft, string>>;

/** The form as the app has it: an unset setting is an empty field. */
export function healthDraftOf(app: Pick<App, "health_path" | "health_expect" | "health_timeout">): HealthDraft {
  return {
    path: app.health_path ?? "",
    expect: app.health_expect ?? "",
    timeout: unset(app.health_timeout) ? "" : String(app.health_timeout),
  };
}

export function sameHealth(a: HealthDraft, b: HealthDraft): boolean {
  return a.path.trim() === b.path.trim() && a.expect.trim() === b.expect.trim() && a.timeout.trim() === b.timeout.trim();
}

const ITEM = /^(\d{3})(?:-(\d{3}))?$/;

function pathProblem(path: string, locale: Locale): string | null {
  if (!path.startsWith("/") || path.startsWith("//")) {
    return translate(locale, "appSettings.healthCheck.pathNoScheme");
  }
  if (path.length > PATH_MAX) return translate(locale, "appSettings.healthCheck.pathTooLong", { max: PATH_MAX });
  // Printable ASCII only, as the gate's request line needs: no space, no control character.
  if (!/^[\x21-\x7e]+$/.test(path)) return translate(locale, "appSettings.healthCheck.pathEncode");
  return null;
}

function expectProblem(expect: string, locale: Locale): string | null {
  for (const raw of expect.split(",")) {
    const match = ITEM.exec(raw.trim());
    if (match === null) return translate(locale, "appSettings.healthCheck.expectHow");
    const low = Number(match[1]);
    const high = match[2] === undefined ? low : Number(match[2]);
    if (!(100 <= low && low <= high && high <= 599)) {
      return translate(locale, "appSettings.healthCheck.expectOutOfRange", { range: raw.trim() });
    }
  }
  return null;
}

/** Reads the health check form, with the backend's own wording for what it would refuse. */
export function parseHealth(draft: HealthDraft, locale: Locale = getLocale()): { values: HealthValues; errors: HealthErrors } {
  const errors: HealthErrors = {};
  const path = draft.path.trim();
  const expect = draft.expect.trim();
  const timeout = draft.timeout.trim();

  if (path !== "") {
    const problem = pathProblem(path, locale);
    if (problem !== null) errors.path = problem;
  }
  if (expect !== "") {
    const problem = expectProblem(expect, locale);
    if (problem !== null) errors.expect = problem;
  }
  let seconds: number | null = null;
  if (timeout !== "") {
    seconds = /^\d+$/.test(timeout) ? Number.parseInt(timeout, 10) : Number.NaN;
    if (!(seconds >= HEALTH_TIMEOUT_MIN && seconds <= HEALTH_TIMEOUT_MAX)) {
      errors.timeout = translate(locale, "appSettings.healthCheck.timeoutRange", {
        min: HEALTH_TIMEOUT_MIN,
        max: HEALTH_TIMEOUT_MAX,
        default: HEALTH_DEFAULTS.timeout,
      });
    }
  }
  return {
    values: { path: path === "" ? null : path, expect: expect === "" ? null : expect, timeout: timeout === "" ? null : seconds },
    errors,
  };
}

/**
 * The field a backend refusal is about. The store's validator answers `400` with one sentence
 * and no `fields` map, so the field is read off the sentence it wrote; a `422` from the
 * request's own validation names it. Null when neither says.
 */
export function healthFieldOf(detail: string): keyof HealthDraft | null {
  if (/timeout/i.test(detail)) return "timeout";
  if (/status/i.test(detail)) return "expect";
  if (/\bpath\b|contains a space/i.test(detail)) return "path";
  return null;
}

function unset(value: unknown): boolean {
  return value === null || value === undefined;
}

/** What the gate asks now, each part marked when it is the default. */
export function effectiveHealth(
  app: Pick<App, "health_path" | "health_expect" | "health_timeout">,
  locale: Locale = getLocale(),
): {
  path: string;
  expect: string;
  timeout: number;
  defaults: Record<keyof HealthDraft, boolean>;
} {
  return {
    path: app.health_path ?? HEALTH_DEFAULTS.path,
    expect: app.health_expect ?? translate(locale, "appSettings.healthCheck.defaultExpect"),
    timeout: app.health_timeout ?? HEALTH_DEFAULTS.timeout,
    defaults: { path: unset(app.health_path), expect: unset(app.health_expect), timeout: unset(app.health_timeout) },
  };
}

/** Reads the retention field: a whole number of releases from 1 to 50. */
export function parseRetention(text: string, locale: Locale = getLocale()): { keep: number | null; error: string | null } {
  const trimmed = text.trim();
  if (!/^\d+$/.test(trimmed)) {
    return { keep: null, error: translate(locale, "appSettings.retention.invalid", { min: RETENTION_MIN, max: RETENTION_MAX }) };
  }
  const keep = Number.parseInt(trimmed, 10);
  if (keep < RETENTION_MIN || keep > RETENTION_MAX) {
    return { keep: null, error: translate(locale, "appSettings.retention.range", { min: RETENTION_MIN, max: RETENTION_MAX }) };
  }
  return { keep, error: null };
}
