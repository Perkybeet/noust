import { createFileRoute } from "@tanstack/react-router";

import { GitLabCallback } from "../../../../features/settings/gitlab/GitLabCallback";

/**
 * Where GitLab sends the browser back after the operator approves a sign-in (`code`, `state`).
 * The path is the OAuth application's redirect URI (noust.integrations.gitlab CALLBACK_PATH),
 * so it cannot move.
 */
export const Route = createFileRoute("/_console/integrations/gitlab/callback")({
  component: GitLabCallback,
});
