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

/**
 * Which of the application's variables a preview is given: only the ones named (`allowlist`,
 * the safe way) or every one but the excluded (`all`, how previews were made before 4.0).
 */
export type InheritMode = "allowlist" | "all";

/**
 * What database a preview gets: the application's own (`shared`), an empty one of its own
 * (`empty`) or one of its own that starts as a copy of the application's (`clone`).
 */
export type DataMode = "shared" | "empty" | "clone";

/** What a pull request from a fork gets: no preview (`refuse`) or one that waits for an operator (`approve`). */
export type ForkPolicy = "refuse" | "approve";

/** The longest domain template the backend accepts (`noust.core.preview_names.MAX_TEMPLATE`). */
export const MAX_TEMPLATE = 200;

/** The placeholders a domain template may hold. */
export const TEMPLATE_PLACEHOLDERS = ["pr_id", "random", "domain", "name", "base"] as const;

/** The template that is the default, written out: what an empty field means. */
export const DEFAULT_TEMPLATE = "pr-{{pr_id}}-{{name}}.{{base}}";

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
  inherit_env: InheritMode;
  /** The variables an `allowlist` preview is given, as typed. */
  allow_env: string;
  data_mode: DataMode;
  /** How a preview is named, as typed; empty is the default. */
  domain_template: string;
  /** Whether previews tell search engines to leave them alone. */
  noindex: boolean;
  fork_policy: ForkPolicy;
}

export type PreviewField = "base_domain" | "max_previews" | "ttl_hours" | "exclude_env" | "allow_env" | "domain_template";

export const PREVIEW_FIELDS: readonly PreviewField[] = ["base_domain", "max_previews", "ttl_hours", "exclude_env", "allow_env", "domain_template"];

export type PreviewErrors = Partial<Record<PreviewField, string>>;

export interface PreviewValues {
  base_domain: string;
  max_previews: number;
  ttl_hours: number;
  allow_bots: boolean;
  exclude_env: string[];
  inherit_env: InheritMode;
  allow_env: string[];
  data_mode: DataMode;
  /** An empty string goes back to the default name; the backend reads null as "keep". */
  domain_template: string;
  noindex: boolean;
  fork_policy: ForkPolicy;
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
    // The safe way from the first frame: a preview is given the variables named, and a database of its own.
    return {
      base_domain: "",
      max_previews: String(PREVIEW_DEFAULTS.max_previews),
      ...ttlDraft(PREVIEW_DEFAULTS.ttl_hours),
      allow_bots: false,
      exclude_env: "",
      inherit_env: "allowlist",
      allow_env: "",
      data_mode: "empty",
      domain_template: "",
      noindex: true,
      fork_policy: "refuse",
    };
  }
  return {
    base_domain: settings.base_domain,
    max_previews: String(settings.max_previews),
    ...ttlDraft(settings.ttl_hours),
    allow_bots: settings.allow_bots,
    exclude_env: settings.exclude_env.join(", "),
    inherit_env: settings.inherit_env === "all" ? "all" : "allowlist",
    allow_env: (settings.allow_env ?? []).join(", "),
    data_mode: settings.data_mode === "shared" ? "shared" : settings.data_mode === "clone" ? "clone" : "empty",
    domain_template: settings.domain_template ?? "",
    noindex: settings.noindex,
    fork_policy: settings.fork_policy === "approve" ? "approve" : "refuse",
  };
}

export function samePreviewDraft(a: PreviewDraft, b: PreviewDraft): boolean {
  return (
    a.base_domain.trim() === b.base_domain.trim() &&
    a.max_previews.trim() === b.max_previews.trim() &&
    hoursOf(a) === hoursOf(b) &&
    a.allow_bots === b.allow_bots &&
    envNamesOf(a.exclude_env).join(",") === envNamesOf(b.exclude_env).join(",") &&
    a.inherit_env === b.inherit_env &&
    envNamesOf(a.allow_env).join(",") === envNamesOf(b.allow_env).join(",") &&
    a.data_mode === b.data_mode &&
    a.domain_template.trim() === b.domain_template.trim() &&
    a.noindex === b.noindex &&
    a.fork_policy === b.fork_policy
  );
}

/** How many of the form's fields say something different from the settings: the save bar's count. */
export function previewChanges(draft: PreviewDraft, stored: PreviewDraft): number {
  return [
    draft.base_domain.trim() !== stored.base_domain.trim(),
    draft.max_previews.trim() !== stored.max_previews.trim(),
    hoursOf(draft) !== hoursOf(stored),
    draft.allow_bots !== stored.allow_bots,
    envNamesOf(draft.exclude_env).join(",") !== envNamesOf(stored.exclude_env).join(","),
    draft.inherit_env !== stored.inherit_env,
    envNamesOf(draft.allow_env).join(",") !== envNamesOf(stored.allow_env).join(","),
    draft.data_mode !== stored.data_mode,
    draft.domain_template.trim() !== stored.domain_template.trim(),
    draft.noindex !== stored.noindex,
    draft.fork_policy !== stored.fork_policy,
  ].filter(Boolean).length;
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

  const allowed = envNamesOf(draft.allow_env);
  const invalidAllowed = allowed.filter((name) => !ENV_NAME.test(name));
  if (invalidAllowed.length > 0) {
    errors.allow_env = translate(locale, "appSettings.previews.excludeInvalid", { count: invalidAllowed.length, list: invalidAllowed.join(", ") });
  }

  const templateError = templateProblem(draft.domain_template, locale);
  if (templateError !== null) errors.domain_template = templateError;

  if (Object.keys(errors).length > 0 || max === null || hours === null) return { values: null, errors };
  return {
    values: {
      base_domain: base,
      max_previews: max,
      ttl_hours: hours,
      allow_bots: draft.allow_bots,
      exclude_env: excluded,
      inherit_env: draft.inherit_env,
      allow_env: allowed,
      data_mode: draft.data_mode,
      domain_template: draft.domain_template.trim(),
      noindex: draft.noindex,
      fork_policy: draft.fork_policy,
    },
    errors,
  };
}

/**
 * The field a refusal without `fields` is about, from the backend's own words: its preview
 * rules raise a plain 400 whose detail names the value it refused.
 */
export function previewFieldOf(detail: string): PreviewField | null {
  if (/base domain|to put previews under|No room for the preview/i.test(detail)) return "base_domain";
  if (/domain template|placeholder/i.test(detail)) return "domain_template";
  if (/preview lives|time-to-live|duration/i.test(detail)) return "ttl_hours";
  if (/previews at once|number of previews/i.test(detail)) return "max_previews";
  if (/allowed|copied from/i.test(detail)) return "allow_env";
  if (/variable name|never copied/i.test(detail)) return "exclude_env";
  return null;
}

const PREVIEW_STATES: Readonly<Record<string, { state: StatusView["state"]; labelKey: MessageKey; attention: boolean }>> = {
  pending: { state: "deploying", labelKey: "appSettings.previews.statusPending", attention: false },
  deploying: { state: "deploying", labelKey: "common.statusPill.deploying", attention: false },
  ready: { state: "running", labelKey: "appSettings.previews.statusReady", attention: false },
  failed: { state: "failed", labelKey: "common.words.failedFeminine", attention: true },
  stopped: { state: "stopped", labelKey: "previewHand.statusStopped", attention: false },
  removing: { state: "deploying", labelKey: "appSettings.previews.statusRemoving", attention: false },
  // Waiting for a person, not for the machine: it needs the operator, so it is told apart from "in progress".
  awaiting: { state: "stopped", labelKey: "rollout.previews.statusAwaiting", attention: true },
};

/** A preview's status in the console's state language; an unknown word is shown as it came. */
export function previewStatus(status: string, locale: Locale = getLocale()): StatusView {
  const known = PREVIEW_STATES[status.trim().toLowerCase()];
  if (known) return { state: known.state, label: translate(locale, known.labelKey), attention: known.attention };
  const word = status.trim();
  return {
    state: "unknown",
    label: word === "" ? translate(locale, "common.statusPill.unknown") : `${word.charAt(0).toUpperCase()}${word.slice(1)}`,
    attention: false,
  };
}

/** What a preview is called on screen: `#12` for a pull request, the branch or tag for one made by hand. */
export function previewName(preview: { kind: string; number: number; ref?: string | null | undefined; branch: string }): string {
  if (preview.kind === "pr") return `#${preview.number}`;
  return preview.ref ?? preview.branch;
}

/**
 * Why a domain template cannot be right, or null when it can. Only what is plainly wrong is
 * caught here, for a quick answer; whether the whole name fits a domain is the backend's to say.
 */
export function templateProblem(template: string, locale: Locale = getLocale()): string | null {
  const text = template.trim();
  if (text === "") return null;
  if (text.length > MAX_TEMPLATE) return translate(locale, "rollout.previews.templateTooLong", { max: MAX_TEMPLATE });
  const used = [...text.matchAll(/\{\{\s*([A-Za-z_]+)\s*\}\}/g)].map((match) => match[1] ?? "");
  const unknown = used.filter((name) => !(TEMPLATE_PLACEHOLDERS as readonly string[]).includes(name));
  if (unknown.length > 0) return translate(locale, "rollout.previews.templateUnknown", { list: unknown.map((name) => `{{${name}}}`).join(", ") });
  if (!used.includes("pr_id") && !used.includes("random")) {
    return translate(locale, "rollout.previews.templateNeedsId", { id: "{{pr_id}}", random: "{{random}}" });
  }
  return null;
}

/**
 * The domain a template gives for pull request 12, to show beside the field. The same
 * placeholders the backend fills (`noust.core.preview_names`), with a sample for the random one.
 */
export function templateExample(template: string, domain: string, base: string): string {
  const text = template.trim() === "" ? DEFAULT_TEMPLATE : template.trim();
  const name = domain.replace(/\./g, "-");
  const values: Record<string, string> = { pr_id: "12", random: "k3x9f2ab", domain, name, base: base.trim() === "" ? "previews.example.com" : base.trim() };
  return text.replace(/\{\{\s*([A-Za-z_]+)\s*\}\}/g, (whole, key: string) => values[key] ?? whole);
}
