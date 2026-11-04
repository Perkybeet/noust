import { getLocale } from "../../app/locale";
import type { Locale } from "../../app/locale";
import { translate } from "../../i18n";
import type { MessageKey } from "../../i18n";
import { asCoded, sentenceKey, serverSentence } from "../../lib/serverSentence";
import { phaseOf, stepOf } from "./deployments/phases";

/** The jobs whose steps are the deploy pipeline's, which the console knows by name. */
const DEPLOY_JOBS = new Set(["deploy", "update", "rollback", "push", "zero_downtime", "migrate"]);

/** The steps of a deploy that are not one of its five phases, as the deployer names them, and their words. */
const FIXED_STEPS: readonly (readonly [RegExp, MessageKey])[] = [
  [/^deploying$/i, "common.statusPill.deploying"],
  [/^preparing deployment$/i, "appPages.job.steps.preparing"],
  [/^registering application$/i, "appPages.job.steps.registering"],
  [/^setting permissions$/i, "appPages.job.steps.permissions"],
  [/^creating site configuration$/i, "appPages.job.steps.site"],
  [/^creating systemd service$/i, "appPages.job.steps.service"],
  [/^obtaining ssl certificate$/i, "appPages.job.steps.certificate"],
  [/^enabling updates without downtime$/i, "appPages.job.steps.zeroDowntime"],
  // The first thing an update of an app on releases says: a plan of two phases, which `phaseOf` reads as neither.
  [/^building and activating a new release$/i, "appPages.job.steps.buildAndActivate"],
];

const PHASE_DOING = {
  fetch: "appPages.deployments.phases.fetch.doing",
  install: "appPages.deployments.phases.install.doing",
  build: "appPages.deployments.phases.build.doing",
  activate: "appPages.deployments.phases.activate.doing",
  health: "appPages.deployments.phases.health.doing",
} as const satisfies Record<string, MessageKey>;

/**
 * What a deploy is doing, in the console's language. The server names its steps in English ("Deploying",
 * "[7/7] Activating release...") and sends no code for them, so the ones a deploy takes are known here by
 * name: the five phases the deployment page draws, and the few steps around them. A step the console does not
 * know, and any step of a job that is not a deploy, is shown as the server said it. In English the server's own
 * words are shown whatever they are.
 *
 * @param type The job's type (the backend's JobType).
 * @param step What the job is doing, as the server says it: its current step or its newest line.
 * @param locale The console's language.
 * @param coded The sentence the server sent for the step (`current_step_coded`), when it did: a step of
 *   any kind of job that has one is said in the console's language, with no need to know it by name.
 */
export function stepWords(type: string, step: string | null | undefined, locale: Locale = getLocale(), coded: unknown = null): string | null {
  if (step === null || step === undefined || step.trim() === "") return null;
  const sentence = asCoded(coded);
  if (sentence !== null && sentenceKey(sentence, "text") !== null) return serverSentence(locale, sentence, step, "text");
  if (locale === "en" || !DEPLOY_JOBS.has(type)) return step;
  const name = (stepOf(step) ?? step).trim().replace(/(?:\.{3}|…)$/, "");
  for (const [pattern, key] of FIXED_STEPS) if (pattern.test(name)) return translate(locale, key);
  const phase = phaseOf(name);
  return phase === null ? step : `${translate(locale, PHASE_DOING[phase])}…`;
}
