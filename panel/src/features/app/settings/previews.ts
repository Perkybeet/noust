import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import type { PreviewSettings } from "../../../api/queries/previews";
import type { StatusView } from "../../../components/page/status";
import { translate } from "../../../i18n/translate";
import type { MessageKey } from "../../../i18n/types";

/** The bounds the backend holds previews to (`noust.managers.previews`). */
export const MAX_PREVIEWS_MIN = 1;
export const MAX_PREVIEWS_MAX = 20;
export const TTL_HOURS_MIN = 1;
export const TTL_HOURS_MAX = 2160;

/** What a new setup starts from: the backend's own defaults (3 at once, 7 days). */
export const PREVIEW_DEFAULTS = { max_previews: 3, ttl_hours: 168 } as const;

export type TtlUnit = "hours" | "days";

/** The settings form, named as the API names the fields so a refusal lands beside the right one. */
export interface PreviewDraft {
  base_domain: string;
  max_previews: string;
  /** The lifetime in `unit`s, as typed. */
  ttl_hours: string;
  unit: TtlUnit;
  /** Whether pull requests opened or pushed to by bot accounts get a preview. */
  allow_bots: boolean;
  /** Variable names never copied to a preview, as typed: separated by commas or spaces. */
  exclude_env: string;
}

export type PreviewField = "base_domain" | "max_previews" | "ttl_hours" | "exclude_env";

export const PREVIEW_FIELDS: readonly PreviewField[] = ["base_domain", "max_previews", "ttl_hours", "exclude_env"];

export type PreviewErrors = Partial<Record<PreviewField, string>>;

export interface PreviewValues {
  base_domain: string;
  max_previews: number;
  ttl_hours: number;
  allow_bots: boolean;
  exclude_env: string[];
}

/** An environment variable name, as the backend's `ENV_NAME_PATTERN` has it. */
const ENV_NAME = /^[A-Za-z_][A-Za-z0-9_]*$/;

/** The names in the "never copied" field: split on commas and white space, each once, in order. */
export function envNamesOf(text: string): string[] {
  const names: string[] = [];
  for (const name of text.split(/[\s,]+/)) {
    if (name !== "" && !names.includes(name)) names.push(name);
  }
  return names;
}

/** A lifetime shown in days when it is whole days, in hours otherwise. */
export function ttlDraft(hours: number): Pick<PreviewDraft, "ttl_hours" | "unit"> {
  return hours % 24 === 0 ? { ttl_hours: String(hours / 24), unit: "days" } : { ttl_hours: String(hours), unit: "hours" };
}

/** The form for the settings an app has, or for turning previews on with the defaults. */
export function previewDraftOf(settings: PreviewSettings | null | undefined): PreviewDraft {
  if (settings === null || settings === undefined) {
    return { base_domain: "", max_previews: String(PREVIEW_DEFAULTS.max_previews), ...ttlDraft(PREVIEW_DEFAULTS.ttl_hours), allow_bots: false, exclude_env: "" };
  }
  return { base_domain: settings.base_domain, max_previews: String(settings.max_previews), ...ttlDraft(settings.ttl_hours), allow_bots: settings.allow_bots, exclude_env: settings.exclude_env.join(", ") };
}

export function samePreviewDraft(a: PreviewDraft, b: PreviewDraft): boolean {
  return (
    a.base_domain.trim() === b.base_domain.trim() &&
    a.max_previews.trim() === b.max_previews.trim() &&
    hoursOf(a) === hoursOf(b) &&
    a.allow_bots === b.allow_bots &&
    envNamesOf(a.exclude_env).join(",") === envNamesOf(b.exclude_env).join(",")
  );
}

/** The lifetime in hours, or null when it is not a whole number. */
function hoursOf(draft: Pick<PreviewDraft, "ttl_hours" | "unit">): number | null {
  const text = draft.ttl_hours.trim();
  if (!/^\d+$/.test(text)) return null;
  const amount = Number.parseInt(text, 10);
  return draft.unit === "days" ? amount * 24 : amount;
}

/** "7 days", "36 hours", "1 hour": a lifetime in the unit that reads best. */
export function lifetime(hours: number, locale: Locale = getLocale()): string {
  if (hours % 24 === 0) return translate(locale, "appSettings.previews.lifetimeDays", { count: hours / 24 });
  return translate(locale, "appSettings.previews.lifetimeHours", { count: hours });
}

/**
 * The form, read as the backend will. Only what is plainly wrong is caught here, for a quick
 * answer; whether the base domain is one previews can live under is the backend's to say.
 */
export function parsePreviewDraft(draft: PreviewDraft, locale: Locale = getLocale()): { values: PreviewValues | null; errors: PreviewErrors } {
  const errors: PreviewErrors = {};
  const base = draft.base_domain.trim();
  if (base === "") {
    errors.base_domain = translate(locale, "appSettings.previews.baseDomainRequired");
  } else if (/\s|\/|:/.test(base)) {
    errors.base_domain = translate(locale, "appSettings.previews.baseDomainNoScheme");
  }

  const maxText = draft.max_previews.trim();
  const max = /^\d+$/.test(maxText) ? Number.parseInt(maxText, 10) : null;
  if (max === null || max < MAX_PREVIEWS_MIN || max > MAX_PREVIEWS_MAX) {
    errors.max_previews = translate(locale, "appSettings.previews.maxRange", { min: MAX_PREVIEWS_MIN, max: MAX_PREVIEWS_MAX });
  }

  const hours = hoursOf(draft);
  if (hours === null || hours < TTL_HOURS_MIN || hours > TTL_HOURS_MAX) {
    errors.ttl_hours =
      draft.unit === "days"
        ? translate(locale, "appSettings.previews.ttlDays")
        : translate(locale, "appSettings.previews.ttlHours", { min: TTL_HOURS_MIN, max: TTL_HOURS_MAX });
  }

  const excluded = envNamesOf(draft.exclude_env);
  const invalid = excluded.filter((name) => !ENV_NAME.test(name));
  if (invalid.length > 0) {
    errors.exclude_env = translate(locale, "appSettings.previews.excludeInvalid", { count: invalid.length, list: invalid.join(", ") });
  }

  if (Object.keys(errors).length > 0 || max === null || hours === null) return { values: null, errors };
  return { values: { base_domain: base, max_previews: max, ttl_hours: hours, allow_bots: draft.allow_bots, exclude_env: excluded }, errors };
}

/**
 * The field a refusal without `fields` is about, from the backend's own words: its preview
 * rules raise a plain 400 whose detail names the value it refused.
 */
export function previewFieldOf(detail: string): PreviewField | null {
  if (/base domain|to put previews under|No room for the preview/i.test(detail)) return "base_domain";
  if (/preview lives|time-to-live|duration/i.test(detail)) return "ttl_hours";
  if (/previews at once|number of previews/i.test(detail)) return "max_previews";
  if (/variable name|never copied/i.test(detail)) return "exclude_env";
  return null;
}

const PREVIEW_STATES: Readonly<Record<string, { state: StatusView["state"]; labelKey: MessageKey; attention: boolean }>> = {
  pending: { state: "deploying", labelKey: "appSettings.previews.statusPending", attention: false },
  deploying: { state: "deploying", labelKey: "appSettings.previews.statusDeploying", attention: false },
  ready: { state: "running", labelKey: "appSettings.previews.statusReady", attention: false },
  failed: { state: "failed", labelKey: "appSettings.previews.statusFailed", attention: true },
  removing: { state: "deploying", labelKey: "appSettings.previews.statusRemoving", attention: false },
};

/** A preview's status in the console's state language; an unknown word is shown as it came. */
export function previewStatus(status: string, locale: Locale = getLocale()): StatusView {
  const known = PREVIEW_STATES[status.trim().toLowerCase()];
  if (known) return { state: known.state, label: translate(locale, known.labelKey), attention: known.attention };
  const word = status.trim();
  return {
    state: "unknown",
    label: word === "" ? translate(locale, "appSettings.previews.statusUnknown") : `${word.charAt(0).toUpperCase()}${word.slice(1)}`,
    attention: false,
  };
}
