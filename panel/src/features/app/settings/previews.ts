import type { PreviewSettings } from "../../../api/queries/previews";
import type { StatusView } from "../../../components/page/status";

/** The bounds the backend holds previews to (`wasm.managers.previews`). */
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
}

export type PreviewField = "base_domain" | "max_previews" | "ttl_hours";

export const PREVIEW_FIELDS: readonly PreviewField[] = ["base_domain", "max_previews", "ttl_hours"];

export type PreviewErrors = Partial<Record<PreviewField, string>>;

export interface PreviewValues {
  base_domain: string;
  max_previews: number;
  ttl_hours: number;
}

/** A lifetime shown in days when it is whole days, in hours otherwise. */
export function ttlDraft(hours: number): Pick<PreviewDraft, "ttl_hours" | "unit"> {
  return hours % 24 === 0 ? { ttl_hours: String(hours / 24), unit: "days" } : { ttl_hours: String(hours), unit: "hours" };
}

/** The form for the settings an app has, or for turning previews on with the defaults. */
export function previewDraftOf(settings: PreviewSettings | null | undefined): PreviewDraft {
  if (settings === null || settings === undefined) {
    return { base_domain: "", max_previews: String(PREVIEW_DEFAULTS.max_previews), ...ttlDraft(PREVIEW_DEFAULTS.ttl_hours) };
  }
  return { base_domain: settings.base_domain, max_previews: String(settings.max_previews), ...ttlDraft(settings.ttl_hours) };
}

export function samePreviewDraft(a: PreviewDraft, b: PreviewDraft): boolean {
  return a.base_domain.trim() === b.base_domain.trim() && a.max_previews.trim() === b.max_previews.trim() && hoursOf(a) === hoursOf(b);
}

/** The lifetime in hours, or null when it is not a whole number. */
function hoursOf(draft: Pick<PreviewDraft, "ttl_hours" | "unit">): number | null {
  const text = draft.ttl_hours.trim();
  if (!/^\d+$/.test(text)) return null;
  const amount = Number.parseInt(text, 10);
  return draft.unit === "days" ? amount * 24 : amount;
}

/** "7 days", "36 hours", "1 hour": a lifetime in the unit that reads best. */
export function lifetime(hours: number): string {
  if (hours % 24 === 0) {
    const days = hours / 24;
    return `${String(days)} ${days === 1 ? "day" : "days"}`;
  }
  return `${String(hours)} ${hours === 1 ? "hour" : "hours"}`;
}

/**
 * The form, read as the backend will. Only what is plainly wrong is caught here, for a quick
 * answer; whether the base domain is one previews can live under is the backend's to say.
 */
export function parsePreviewDraft(draft: PreviewDraft): { values: PreviewValues | null; errors: PreviewErrors } {
  const errors: PreviewErrors = {};
  const base = draft.base_domain.trim();
  if (base === "") {
    errors.base_domain = "Give the domain the wildcard record is for, such as previews.example.com.";
  } else if (/\s|\/|:/.test(base)) {
    errors.base_domain = "A domain name only, such as previews.example.com: no scheme, port or path.";
  }

  const maxText = draft.max_previews.trim();
  const max = /^\d+$/.test(maxText) ? Number.parseInt(maxText, 10) : null;
  if (max === null || max < MAX_PREVIEWS_MIN || max > MAX_PREVIEWS_MAX) {
    errors.max_previews = `From ${String(MAX_PREVIEWS_MIN)} to ${String(MAX_PREVIEWS_MAX)} previews at once.`;
  }

  const hours = hoursOf(draft);
  if (hours === null || hours < TTL_HOURS_MIN || hours > TTL_HOURS_MAX) {
    errors.ttl_hours = draft.unit === "days" ? "From 1 to 90 days." : `From ${String(TTL_HOURS_MIN)} to ${String(TTL_HOURS_MAX)} hours (90 days).`;
  }

  if (Object.keys(errors).length > 0 || max === null || hours === null) return { values: null, errors };
  return { values: { base_domain: base, max_previews: max, ttl_hours: hours }, errors };
}

/**
 * The field a refusal without `fields` is about, from the backend's own words: its preview
 * rules raise a plain 400 whose detail names the value it refused.
 */
export function previewFieldOf(detail: string): PreviewField | null {
  if (/base domain|to put previews under|No room for the preview/i.test(detail)) return "base_domain";
  if (/preview lives|time-to-live|duration/i.test(detail)) return "ttl_hours";
  if (/previews at once|number of previews/i.test(detail)) return "max_previews";
  return null;
}

const PREVIEW_STATES: Readonly<Record<string, StatusView>> = {
  pending: { state: "deploying", label: "Pending", attention: false },
  deploying: { state: "deploying", label: "Deploying", attention: false },
  ready: { state: "running", label: "Ready", attention: false },
  failed: { state: "failed", label: "Failed", attention: true },
  removing: { state: "deploying", label: "Removing", attention: false },
};

/** A preview's status in the console's state language; an unknown word is shown as it came. */
export function previewStatus(status: string): StatusView {
  const known = PREVIEW_STATES[status.trim().toLowerCase()];
  if (known) return known;
  const word = status.trim();
  return { state: "unknown", label: word === "" ? "Unknown" : `${word.charAt(0).toUpperCase()}${word.slice(1)}`, attention: false };
}
