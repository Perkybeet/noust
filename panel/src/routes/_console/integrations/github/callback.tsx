import { createFileRoute } from "@tanstack/react-router";

import { GitHubCallback } from "../../../../features/settings/github/GitHubCallback";

/**
 * Where GitHub sends the browser back: after creating the App (`code`, `state`) and after
 * installing it (`installation_id`, `setup_action`). The path is the App's redirect and setup
 * URL (noust.integrations.github.manifest.CALLBACK_PATH), so it cannot move.
 */
export const Route = createFileRoute("/_console/integrations/github/callback")({
  component: GitHubCallback,
});
