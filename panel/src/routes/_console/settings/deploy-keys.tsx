import { createFileRoute } from "@tanstack/react-router";

import { DeployKeysByServer } from "../../../features/settings/ownTables/OwnSections";

/** Settings > Deploy keys: the SSH keys repositories are cloned with, and the host keys pinned. */
export const Route = createFileRoute("/_console/settings/deploy-keys")({
  component: DeployKeysByServer,
});
