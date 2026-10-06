/**
 * Where a code host keeps what the Source settings point the operator at. The host and the
 * repository are the backend's own reading of the application's source (`webhook.forge`, one
 * classification of hosts, shared with the webhook setup); only the address of the page where
 * a deploy key is added is written here, because it is a fact about the host's website, not
 * about the server.
 */

import type { WebhookStatus } from "./queries";

export type ForgeFacts = WebhookStatus["forge"];

/**
 * The page of the repository where a deploy key is added, or null when the host is not one
 * Noust knows the layout of (a Git server of the operator's own).
 *
 * @param forge What the webhook status says about the repository's host.
 */
export function deployKeysUrl(forge: ForgeFacts | undefined): string | null {
  const kind = forge?.forge ?? null;
  const host = forge?.host ?? null;
  const repository = forge?.repository ?? null;
  if (kind === null || host === null || repository === null || host === "" || repository === "") return null;
  switch (kind) {
    case "github":
      return `https://github.com/${repository}/settings/keys/new`;
    case "gitlab":
      return `https://${host}/${repository}/-/settings/repository`;
    case "gitea":
      return `https://${host}/${repository}/settings/keys`;
    case "bitbucket":
      return `https://${host}/${repository}/admin/access-keys/`;
    default:
      return null;
  }
}
