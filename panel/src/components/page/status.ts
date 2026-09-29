import { getLocale } from "../../app/locale";
import { translate } from "../../i18n";
import type { Locale, PlainKey } from "../../i18n";
import type { Status } from "../ui/StatusPill";

/** What the console shows for a backend state word, and whether it is a problem. */
export interface StatusView {
  /** The StatusPill state: colour and shape. */
  state: Status;
  /** The word on screen, in the active language (a word the console does not know, verbatim). */
  label: string;
  /** True when an operator should look at it: it belongs under "Needs attention". */
  attention: boolean;
}

/** A word the console knows: its state, the catalog key of its label, and its attention. */
interface KnownState {
  state: Status;
  labelKey: PlainKey;
  attention: boolean;
}

/**
 * Every word the backend uses for an application's state, from three sources that grew
 * separately:
 *
 * - `GET /api/apps` and the `app` event: `running`, `restarting`, `no_answer`, `stopped`,
 *   `failed`, `static`, `unknown` (resolved from systemd), and `deploying` while a deploy or
 *   update job runs;
 * - the store's `AppStatus`: `deploying`, `running`, `stopped`, `failed`, `unknown`;
 * - `noust.core.app_state` (what `noust list` and `noust health` print): `Running`,
 *   `Restarting`, `No answer`, `Stopped`, `Failed`, `Static`, `Unknown`.
 *
 * Matching is case-insensitive. A stopped app is not a problem by itself (an operator stops
 * apps on purpose); a crash loop, a unit systemd gave up on, or a port nothing answers on is.
 */
const APP_STATES: Readonly<Record<string, KnownState>> = {
  running: { state: "running", labelKey: "common.appState.running", attention: false },
  active: { state: "running", labelKey: "common.appState.running", attention: false },
  static: { state: "static", labelKey: "common.appState.static", attention: false },
  deploying: { state: "deploying", labelKey: "common.appState.deploying", attention: false },
  building: { state: "deploying", labelKey: "common.appState.building", attention: false },
  restarting: { state: "deploying", labelKey: "common.appState.restarting", attention: true },
  activating: { state: "deploying", labelKey: "common.appState.starting", attention: false },
  stopped: { state: "stopped", labelKey: "common.appState.stopped", attention: false },
  inactive: { state: "stopped", labelKey: "common.appState.stopped", attention: false },
  failed: { state: "failed", labelKey: "common.appState.failed", attention: true },
  "no answer": { state: "failed", labelKey: "common.appState.noAnswer", attention: true },
  no_answer: { state: "failed", labelKey: "common.appState.noAnswer", attention: true },
  unknown: { state: "unknown", labelKey: "common.appState.unknown", attention: true },
};

function known(view: KnownState, locale: Locale): StatusView {
  return { state: view.state, label: translate(locale, view.labelKey), attention: view.attention };
}

function capitalise(text: string): string {
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/**
 * Maps an application's status to what the console draws. A word the console does not know
 * is still shown, verbatim, with the unknown shape: the backend's word beats a guess.
 *
 * A component passes `t.locale`, so the label follows a language switch.
 */
export function appStatus(status: string | null | undefined, locale: Locale = getLocale()): StatusView {
  const word = status?.trim() ?? "";
  if (word === "") return { state: "unknown", label: translate(locale, "common.appState.unknown"), attention: false };
  const view = APP_STATES[word.toLowerCase()];
  return view ? known(view, locale) : { state: "unknown", label: capitalise(word), attention: false };
}

/**
 * The `DeploymentStatus` enum (`queued`, `running`, `success`, `failed`, `rolled_back`) and
 * the job statuses (`pending`, `running`, `completed`, `failed`, `cancelled`), drawn in the
 * same state language as applications.
 */
const DEPLOY_STATES: Readonly<Record<string, KnownState>> = {
  queued: { state: "deploying", labelKey: "common.deployState.queued", attention: false },
  pending: { state: "deploying", labelKey: "common.deployState.queued", attention: false },
  running: { state: "deploying", labelKey: "common.deployState.inProgress", attention: false },
  success: { state: "running", labelKey: "common.deployState.succeeded", attention: false },
  completed: { state: "running", labelKey: "common.deployState.succeeded", attention: false },
  failed: { state: "failed", labelKey: "common.deployState.failed", attention: true },
  rolled_back: { state: "stopped", labelKey: "common.deployState.rolledBack", attention: true },
  cancelled: { state: "stopped", labelKey: "common.deployState.cancelled", attention: false },
};

/** Maps a deployment's or a job's status to what the console draws, in `locale`. */
export function deployStatus(status: string | null | undefined, locale: Locale = getLocale()): StatusView {
  const word = status?.trim() ?? "";
  if (word === "") return { state: "unknown", label: translate(locale, "common.deployState.unknown"), attention: false };
  const view = DEPLOY_STATES[word.toLowerCase()];
  return view ? known(view, locale) : { state: "unknown", label: capitalise(word.replace(/_/g, " ")), attention: false };
}

/** Severity order for sorting: problems first, then work in progress, then the quiet states. */
export const STATE_RANK: Readonly<Record<Status, number>> = {
  failed: 0,
  unknown: 1,
  warning: 2,
  deploying: 3,
  running: 4,
  static: 5,
  stopped: 6,
};
