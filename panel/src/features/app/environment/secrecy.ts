/**
 * Why WASM treats one environment variable as a secret or not, in the operator's own words,
 * and the three choices an operator can make about it.
 *
 * Mirrors `wasm.core.secret_detection.classify`: GET /api/apps/{domain}/env answers `secret`
 * (whether the value is masked), `reason` (why, in the classifier's own vocabulary) and
 * `marked` (whether an operator's own choice, rather than the name or the value, is why) for
 * every variable. PUT /api/apps/{domain}/env/marks sets or clears the operator's own choice:
 * `true` (always hidden), `false` (always shown), or `null` (back to automatic).
 */

import type { AppEnv } from "../../../api/queries/apps";

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
 * `wasm.core.secret_detection._value_pattern_kind` returns, sent over the wire as
 * `"value: <kind>"`.
 */
const VALUE_KIND_PHRASES: Readonly<Record<string, string>> = {
  stripe: "a Stripe key",
  "github token": "a GitHub token",
  "gitlab token": "a GitLab token",
  "slack token": "a Slack token",
  "aws access key": "an AWS access key",
  "google api key": "a Google API key",
  "sendgrid api key": "a SendGrid API key",
  "twilio credential": "a Twilio credential",
  "api key": "an API key",
  "private key": "a private key",
  jwt: "a JSON Web Token",
  "high entropy": "a random secret",
};

/** The second half of `secrecyLine`: `classify`'s `reason`, decoded to plain words. */
function secrecyWhy(reason: string): string {
  switch (reason) {
    case "marked secret":
      return "marked secret by you";
    case "marked not secret":
      return "marked not secret by you";
    case "name":
      return "its name suggests a secret";
    case "url credentials":
      return "the URL carries credentials";
    case "plain":
      return "nothing about it looks like a secret";
    default:
      if (reason.startsWith("value: ")) {
        const kind = reason.slice("value: ".length);
        return `its value looks like ${VALUE_KIND_PHRASES[kind] ?? "a secret"}`;
      }
      // An unrecognised reason is shown verbatim rather than guessed at.
      return reason;
  }
}

/** "Hidden: its value looks like a Stripe key" - whether a value is hidden, and why. */
export function secrecyLine(verdict: EnvSecrecy): string {
  return `${verdict.secret ? "Hidden" : "Shown"}: ${secrecyWhy(verdict.reason)}`;
}

const STATE_LABEL: Readonly<Record<SecrecyChoice, string>> = {
  secret: "always hidden",
  "not-secret": "always shown",
  auto: "decided automatically",
};

/** How a choice reads as the current state: "always hidden", for a trigger's own label. */
export function secrecyStateLabel(choice: SecrecyChoice): string {
  return STATE_LABEL[choice];
}

const ACTION_LABEL: Readonly<Record<SecrecyChoice, string>> = {
  secret: "Treat as secret",
  "not-secret": "Treat as not secret",
  auto: "Decide automatically",
};

/** What choosing an option does, as a menu item's own words. */
export function secrecyActionLabel(choice: SecrecyChoice): string {
  return ACTION_LABEL[choice];
}

/** What changed, once a mark is saved - for the tab's live region. */
export function secrecyMarkAnnouncement(name: string, mark: boolean | null): string {
  if (mark === true) return `${name} is now always hidden`;
  if (mark === false) return `${name} is now always shown`;
  return `${name} is now classified automatically`;
}
