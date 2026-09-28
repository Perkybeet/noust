/**
 * The GitHub integration as the console presents it, apart from any page: what GitHub's
 * callback asked for, what an installation covers in words, and whether GitHub can deliver
 * the App's events. Pure, so every rule is tested without rendering.
 */

import type { GitHubStatus } from "../../../api/queries/github";

/** Where GitHub sends the browser back: the App's redirect and setup URL (manifest.CALLBACK_PATH). */
export const CALLBACK_PATH = "/integrations/github/callback";

export type CallbackRequest =
  /** The App was just created: exchange the one-time code for its credentials. */
  | { kind: "conversion"; code: string; state: string }
  /** The App was installed on an account, or its repositories changed. */
  | { kind: "installation"; installationId: number; setupAction: string | null }
  /** An organisation member asked for the App; an owner still has to approve it. */
  | { kind: "requested" }
  /** Nothing GitHub would send: opened by hand, or a link cut short. */
  | { kind: "invalid" };

/**
 * What GitHub's callback carries, read from the raw query string. Raw on purpose: the
 * router's search parsing reads values as JSON, which would turn a code or state that
 * happens to look like a number (`1e5`) into a different string.
 */
export function parseCallback(search: string): CallbackRequest {
  const params = new URLSearchParams(search);
  const code = params.get("code");
  const state = params.get("state");
  if (code !== null && code !== "" && state !== null && state !== "") return { kind: "conversion", code, state };
  const installation = params.get("installation_id");
  const setupAction = params.get("setup_action");
  if (installation !== null && /^\d+$/.test(installation)) {
    return { kind: "installation", installationId: Number(installation), setupAction };
  }
  if (setupAction === "request") return { kind: "requested" };
  return { kind: "invalid" };
}

/** An installation's account kind, in words. */
export function accountTypeWords(type: string | null | undefined): string {
  if (type === "Organization") return "Organization";
  if (type === "User") return "Personal account";
  return type ?? "Account";
}

/** Which repositories an installation lets the App read, in words. */
export function repositorySelectionWords(selection: string | null | undefined): string {
  if (selection === "all") return "All repositories";
  if (selection === "selected") return "Selected repositories";
  return "Repositories not reported";
}

export type HooksState =
  /** No public hooks URL: GitHub has nowhere to deliver to. */
  | "unexposed"
  /** A URL, but the App's webhook is not active there yet. */
  | "inactive"
  /** GitHub delivers the App's events here. */
  | "active"
  /** A URL and no App yet: the App will be created with it. */
  | "ready";

export function hooksState(status: Pick<GitHubStatus, "configured" | "hooks_url" | "hooks_active">): HooksState {
  if (status.hooks_url === null || status.hooks_url === undefined || status.hooks_url === "") return "unexposed";
  if (!status.configured) return "ready";
  return status.hooks_active ? "active" : "inactive";
}

/**
 * An organisation's login as GitHub allows it (1 to 39 letters, digits and single hyphens,
 * not starting or ending with one), or why it is not. Empty is fine: the operator's own
 * account.
 */
export function organizationProblem(value: string): string | null {
  const name = value.trim();
  if (name === "") return null;
  if (name.length > 39 || !/^[A-Za-z0-9](?:[A-Za-z0-9]|-(?=[A-Za-z0-9]))*$/.test(name)) {
    return "An organization's name on GitHub uses letters, digits and single hyphens, up to 39 characters, as in its URL: github.com/your-org.";
  }
  return null;
}

/** `owner/repo` split in two; null for anything else. */
export function splitFullName(fullName: string): { owner: string; repo: string } | null {
  const [owner, repo, ...rest] = fullName.split("/");
  if (owner === undefined || repo === undefined || owner === "" || repo === "" || rest.length > 0) return null;
  return { owner, repo };
}
