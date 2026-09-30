/**
 * The words and states of the guided webhook setup: where the connection stands, what each
 * delivery came to, and what the forge is called. Pure, with a trailing `locale` like the
 * other helpers of this folder, so a test reads English and a component passes `t.locale`.
 */

import { getLocale } from "../../../app/locale";
import type { Locale } from "../../../app/locale";
import type { Status } from "../../../components/ui/StatusPill";
import { translate } from "../../../i18n/translate";
import type { PlainKey } from "../../../i18n";

export interface StateView {
  state: Status;
  label: string;
}

const CONNECTION: Readonly<Record<string, { state: Status; label: PlainKey }>> = {
  disabled: { state: "stopped", label: "appSettings.webhook.stateOff" },
  waiting: { state: "queued", label: "appSettings.webhook.stateWaiting" },
  connected: { state: "running", label: "appSettings.webhook.stateConnected" },
  problem: { state: "failed", label: "appSettings.webhook.stateProblem" },
};

/**
 * Where the connection stands: off (grey, stopped on purpose), waiting for the forge's first
 * delivery (a still, dashed ring: nothing is being done yet), connected, or a problem when the
 * newest delivery was refused.
 */
export function connectionView(state: string, locale: Locale = getLocale()): StateView {
  const known = CONNECTION[state];
  if (known === undefined) return { state: "unknown", label: state };
  return { state: known.state, label: translate(locale, known.label) };
}

const OUTCOME: Readonly<Record<string, { state: Status; label: PlainKey }>> = {
  deploy_started: { state: "running", label: "appSettings.webhook.outcomeDeployStarted" },
  preview_started: { state: "running", label: "appSettings.webhook.outcomePreviewStarted" },
  ping: { state: "running", label: "appSettings.webhook.outcomePing" },
  ignored_branch: { state: "stopped", label: "appSettings.webhook.outcomeIgnoredBranch" },
  ignored_event: { state: "stopped", label: "appSettings.webhook.outcomeIgnoredEvent" },
  ignored_pull_request: { state: "stopped", label: "appSettings.webhook.outcomeIgnoredPullRequest" },
  duplicate: { state: "stopped", label: "appSettings.webhook.outcomeDuplicate" },
  bad_signature: { state: "failed", label: "appSettings.webhook.outcomeBadSignature" },
  locked: { state: "failed", label: "appSettings.webhook.outcomeLocked" },
};

/**
 * What one delivery came to: accepted (a deploy, a preview, the forge's ping), ignored (a push
 * to another branch, a repeat) or refused (a wrong signature). An outcome this console does
 * not know is shown as the backend named it.
 */
export function outcomeView(outcome: string, locale: Locale = getLocale()): StateView {
  const known = OUTCOME[outcome];
  if (known === undefined) return { state: "unknown", label: outcome };
  return { state: known.state, label: translate(locale, known.label) };
}

const FORGES: Readonly<Record<string, string>> = { github: "GitHub", gitlab: "GitLab", gitea: "Gitea" };

/** The forge by its product name, which is never translated; a generic name for another host. */
export function forgeName(forge: string | null | undefined, locale: Locale = getLocale()): string {
  return (forge !== null && forge !== undefined ? FORGES[forge] : undefined) ?? translate(locale, "appSettings.webhook.anyForge");
}
