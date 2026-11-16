import { createFileRoute } from "@tanstack/react-router";

import { DeployHerePage } from "../../../features/server/deploy-here/DeployHerePage";

/**
 * What it means to give a central the machine's Docker, how it is turned on (in its compose file, never from the console), what was
 * checked on the machine and what Noust can and cannot do there. Not nested in the Server tabs: it is not about the machine's own
 * state, and its header carries its own.
 */
export const Route = createFileRoute("/_console/server_/deploy-here")({
  component: DeployHerePage,
});
