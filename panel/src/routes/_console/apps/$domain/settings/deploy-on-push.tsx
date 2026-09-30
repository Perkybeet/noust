import { createFileRoute } from "@tanstack/react-router";

import { DeployOnPush } from "../../../../../features/app/settings/DeployOnPush";

/** An application's settings, Deploy on push: the deploy webhook, set up step by step. */
export const Route = createFileRoute("/_console/apps/$domain/settings/deploy-on-push")({
  component: DeployOnPush,
});
