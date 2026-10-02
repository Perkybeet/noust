/**
 * How deployments and releases are named on screen. The backend's words are kept where they
 * are the operator's words too (a commit, a release id); the enum values are not.
 */

import { GitPullRequestArrow, MousePointerClick, SquareTerminal } from "lucide-react";
import type { LucideIcon } from "lucide-react";

import type { BadgeTone } from "../../../components/ui/Badge";
import type { T } from "../../../i18n";

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
      return { label: t("appPages.deployments.words.trigger.cli.label"), by: t("appPages.deployments.words.trigger.cli.by"), icon: SquareTerminal };
    default:
      return { label: trigger, by: trigger, icon: SquareTerminal };
  }
}

export interface ReleaseBadge {
  label: string;
  tone: BadgeTone;
}

/**
 * `ReleaseStatus`, as a badge. Only the one serving and the one that failed are coloured:
 * they are the two an operator acts on; the rest are history.
 */
export function releaseBadge(t: T, status: string): ReleaseBadge {
  switch (status) {
    case "active":
      return { label: t("appPages.deployments.words.release.active"), tone: "ok" };
    case "failed":
      return { label: t("appPages.deployments.words.release.failed"), tone: "fail" };
    case "rolled_back":
      return { label: t("appPages.deployments.words.release.rolledBack"), tone: "neutral" };
    case "superseded":
      return { label: t("appPages.deployments.words.release.superseded"), tone: "neutral" };
    case "built":
      return { label: t("appPages.deployments.words.release.built"), tone: "neutral" };
    default:
      return { label: status.replace(/_/g, " "), tone: "neutral" };
  }
}

/** The short form of a commit, as git prints it. */
export function shortCommit(commit: string | null | undefined): string | null {
  return commit ? commit.slice(0, 7) : null;
}

/**
 * A deployment's status word for its pill: one that succeeded but carries warnings (a
 * post_deploy hook failed once it served) is "deployed with warnings", not a plain success.
 */
export function deploymentStatusWord(deployment: { status: string; warnings?: string | null }): string {
  const succeeded = deployment.status === "success" || deployment.status === "completed";
  return succeeded && deployment.warnings ? "deployed_with_warnings" : deployment.status;
}
