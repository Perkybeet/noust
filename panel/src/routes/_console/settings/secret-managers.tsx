import { createFileRoute } from "@tanstack/react-router";

import { SecretManagersByServer } from "../../../features/settings/ownTables/OwnSections";

export const Route = createFileRoute("/_console/settings/secret-managers")({
  component: SecretManagersByServer,
});
