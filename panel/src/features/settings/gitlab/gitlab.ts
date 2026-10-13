import type { GitLabConnection, GitLabOAuthApp, GitLabStatus } from "../../../api/queries/gitlab";

/** Where GitLab sends the browser back: the console's callback (noust.integrations.gitlab CALLBACK_PATH). */
export const CALLBACK_PATH = "/integrations/gitlab/callback";

/** A host as it is compared: without a scheme, a path or a trailing dot, in lower case. */
export function normalizeHost(value: string): string {
  return value
    .trim()
    .toLowerCase()
    .replace(/^https?:\/\//, "")
    .replace(/\/.*$/, "")
    .replace(/\.$/, "");
}

/** The OAuth application registered for a host, if there is one. */
export function oauthAppFor(status: Pick<GitLabStatus, "oauth_apps">, host: string): GitLabOAuthApp | undefined {
  const wanted = normalizeHost(host);
  return wanted === "" ? undefined : status.oauth_apps.find((app) => normalizeHost(app.host) === wanted);
}

export type ConnectionState = "ok" | "failed" | "unchecked";

/** What the last check said about a connection. */
export function connectionState(connection: Pick<GitLabConnection, "last_status">): ConnectionState {
  if (connection.last_status === "ok") return "ok";
  if (connection.last_status === "failed") return "failed";
  return "unchecked";
}

/**
 * Sends the browser to GitLab to approve the sign-in. Only to an https address: the server
 * names GitLab's, and anything else is not somewhere to send this browser.
 */
export function goToGitLab(url: string): boolean {
  if (!url.startsWith("https://")) return false;
  window.location.assign(url);
  return true;
}

/** What GitLab's redirect carries: the one-time `code` and the `state` the sign-in started with. */
export type GitLabCallbackRequest = { kind: "finish"; code: string; state: string } | { kind: "invalid" };

/**
 * Read from the raw query string, like GitHub's callback: the router's own parsing reads
 * values as JSON, which would turn a code or state that looks like a number into another string.
 */
export function parseGitLabCallback(search: string): GitLabCallbackRequest {
  const params = new URLSearchParams(search);
  const code = params.get("code");
  const state = params.get("state");
  if (code !== null && code !== "" && state !== null && state !== "") return { kind: "finish", code, state };
  return { kind: "invalid" };
}
