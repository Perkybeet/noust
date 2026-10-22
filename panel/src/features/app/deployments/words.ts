/**
 * How deployments and releases are named on screen. The backend's words are kept where they
 * are the operator's words too (a commit, a release id); the enum values are not.
 */

import { GitPullRequestArrow, MousePointerClick } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { T } from "../../../i18n";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";

export interface TriggerWords {
  label: string;
  /** Who or what started it, for a sentence: "started by a push". */
  by: string;
  icon: LucideIcon;
}

/** `DeploymentTrigger`: what started a deploy. */
export function triggerWords(t: T, trigger: string): TriggerWords {
  switch (trigger) {
    case "webhook":
      return { label: t("appPages.deployments.words.trigger.webhook.label"), by: t("appPages.deployments.words.trigger.webhook.by"), icon: GitPullRequestArrow };
    case "panel":
      return { label: t("appPages.deployments.words.trigger.panel.label"), by: t("appPages.deployments.words.trigger.panel.by"), icon: MousePointerClick };
    case "cli":
      return { label: t("appPages.deployments.words.trigger.cli.label"), by: t("appPages.deployments.words.trigger.cli.by"), icon: OBJECT_ICONS.terminal };
    default:
      return { label: trigger, by: trigger, icon: OBJECT_ICONS.terminal };
  }
}

/** The short form of a commit, as git prints it. */
export function shortCommit(commit: string | null | undefined): string | null {
  return commit ? commit.slice(0, 7) : null;
}

/**
 * A deployment's status word for its pill: one that succeeded but carries warnings (a
 * post_deploy hook failed once it served) is "deployed with warnings", not a plain success,
 * and one an operator cancelled is "cancelled": the store keeps its status as `failed` (the
 * column predates cancelling), but nothing went wrong and nothing that served was touched, so
 * it is never drawn, counted or raised as a failure.
 */
export function deploymentStatusWord(deployment: { status: string; warnings?: string | null; cancelled?: boolean | null }): string {
  if (deployment.cancelled === true) return "cancelled";
  const succeeded = deployment.status === "success" || deployment.status === "completed";
  return succeeded && deployment.warnings ? "deployed_with_warnings" : deployment.status;
}
