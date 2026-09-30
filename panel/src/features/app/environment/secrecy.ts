/**
 * Why Noust treats one environment variable as a secret or not, in the operator's own words,
 * and the three choices an operator can make about it.
 *
 * Mirrors `noust.core.secret_detection.classify`: GET /api/apps/{domain}/env answers `secret`
 * (whether the value is masked), `reason` (why, in the classifier's own vocabulary) and
 * `marked` (whether an operator's own choice, rather than the name or the value, is why) for
 * every variable. PUT /api/apps/{domain}/env/marks sets or clears the operator's own choice:
 * `true` (always hidden), `false` (always shown), or `null` (back to automatic).
 */

import type { AppEnv } from "../../../api/queries/apps";
import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import { translate } from "../../../i18n/translate";
import type { MessageKey } from "../../../i18n/types";

export type EnvSecrecy = NonNullable<AppEnv["secrets"]>[string];

/** The three things an operator can set a variable to; `auto` is "no mark", the default. */
export type SecrecyChoice = "secret" | "not-secret" | "auto";

/** Which of the three choices a verdict reflects. */
export function secrecyChoice(verdict: EnvSecrecy): SecrecyChoice {
  if (!verdict.marked) return "auto";
  return verdict.secret ? "secret" : "not-secret";
}

/** The mark to send PUT /env/marks for a choice: `null` clears it, going back to automatic. */
export function markFor(choice: SecrecyChoice): boolean | null {
  if (choice === "secret") return true;
  if (choice === "not-secret") return false;
  return null;
}

/**
 * What a value pattern the classifier matched is called, in words an operator did not have to
 * learn the classifier's vocabulary to read. Keys are exactly the kinds
 * `noust.core.secret_detection._value_pattern_kind` returns, sent over the wire as
 * `"value: <kind>"`.
 */
const VALUE_KIND_KEYS: Readonly<Record<string, MessageKey>> = {
  stripe: "environment.secrecy.valueKindStripe",
  "github token": "environment.secrecy.valueKindGithubToken",
  "gitlab token": "environment.secrecy.valueKindGitlabToken",
  "slack token": "environment.secrecy.valueKindSlackToken",
  "aws access key": "environment.secrecy.valueKindAwsAccessKey",
  "google api key": "environment.secrecy.valueKindGoogleApiKey",
  "sendgrid api key": "environment.secrecy.valueKindSendgridApiKey",
  "twilio credential": "environment.secrecy.valueKindTwilioCredential",
  "api key": "environment.secrecy.valueKindApiKey",
  "private key": "environment.secrecy.valueKindPrivateKey",
  jwt: "environment.secrecy.valueKindJwt",
  "high entropy": "environment.secrecy.valueKindHighEntropy",
};

/** The second half of `secrecyLine`: `classify`'s `reason`, decoded to plain words. */
function secrecyWhy(reason: string, locale: Locale): string {
  switch (reason) {
    case "marked secret":
      return translate(locale, "environment.secrecy.reasonMarkedSecret");
    case "marked not secret":
      return translate(locale, "environment.secrecy.reasonMarkedNotSecret");
    case "name":
      return translate(locale, "environment.secrecy.reasonName");
    case "url credentials":
      return translate(locale, "environment.secrecy.reasonUrlCredentials");
    case "plain":
      return translate(locale, "environment.secrecy.reasonPlain");
    default:
      if (reason.startsWith("value: ")) {
        const kind = reason.slice("value: ".length);
        const key = VALUE_KIND_KEYS[kind];
        return translate(locale, "environment.secrecy.reasonValue", {
          kind: key ? translate(locale, key) : translate(locale, "environment.secrecy.valueKindFallback"),
        });
      }
      // An unrecognised reason is shown verbatim rather than guessed at.
      return reason;
  }
}

/** "Hidden: its value looks like a Stripe key" - whether a value is hidden, and why. */
export function secrecyLine(verdict: EnvSecrecy, locale: Locale = getLocale()): string {
  return translate(locale, verdict.secret ? "environment.secrecy.lineHidden" : "environment.secrecy.lineShown", {
    why: secrecyWhy(verdict.reason, locale),
  });
}

/** How a choice reads as the current state: "always hidden", for a trigger's own label. */
export function secrecyStateLabel(choice: SecrecyChoice, locale: Locale = getLocale()): string {
  if (choice === "secret") return translate(locale, "environment.secrecy.stateSecret");
  if (choice === "not-secret") return translate(locale, "environment.secrecy.stateNotSecret");
  return translate(locale, "environment.secrecy.stateAuto");
}

/** What choosing an option does, as a menu item's own words. */
export function secrecyActionLabel(choice: SecrecyChoice, locale: Locale = getLocale()): string {
  if (choice === "secret") return translate(locale, "environment.secrecy.actionSecret");
  if (choice === "not-secret") return translate(locale, "environment.secrecy.actionNotSecret");
  return translate(locale, "environment.secrecy.actionAuto");
}

/** What changed, once a mark is saved - for the tab's live region. */
export function secrecyMarkAnnouncement(name: string, mark: boolean | null, locale: Locale = getLocale()): string {
  if (mark === true) return translate(locale, "environment.secrecy.announceHidden", { name });
  if (mark === false) return translate(locale, "environment.secrecy.announceShown", { name });
  return translate(locale, "environment.secrecy.announceAuto", { name });
}

export interface SecrecyKind {
  /** Whether the server hides the value. */
  secret: boolean;
  /** "Secret" or "Plain": the variable's type, as the table's Type column names it. */
  label: string;
  /** Why, in a few words ("by its name", "marked by you"); null for a plain value nothing marked. */
  reason: string | null;
}

/**
 * A variable's type in the table: secret or plain, and in a few words what decided it. The full
 * sentence (`secrecyLine`) is the cell's title.
 */
export function secrecyKind(verdict: EnvSecrecy, locale: Locale = getLocale()): SecrecyKind {
  const label = translate(locale, verdict.secret ? "environment.secrecy.typeSecret" : "environment.secrecy.typePlain");
  let reason: string | null;
  switch (verdict.reason) {
    case "marked secret":
    case "marked not secret":
      reason = translate(locale, "environment.secrecy.shortMarked");
      break;
    case "name":
      reason = translate(locale, "environment.secrecy.shortName");
      break;
    case "url credentials":
      reason = translate(locale, "environment.secrecy.shortUrlCredentials");
      break;
    case "plain":
      reason = null;
      break;
    default:
      reason = verdict.reason.startsWith("value: ") ? translate(locale, "environment.secrecy.shortValue") : verdict.reason;
  }
  return { secret: verdict.secret, label, reason };
}
