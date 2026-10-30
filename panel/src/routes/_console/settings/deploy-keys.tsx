import { createFileRoute } from "@tanstack/react-router";

import { DeployKeysSettings } from "../../../features/settings/DeployKeysSettings";

/** Settings > Deploy keys: the SSH keys repositories are cloned with, and the host keys pinned. */
export const Route = createFileRoute("/_console/settings/deploy-keys")({
  component: DeployKeysSettings,
});
