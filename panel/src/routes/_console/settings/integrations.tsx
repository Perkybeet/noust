import { createFileRoute } from "@tanstack/react-router";

import { IntegrationsByServer } from "../../../features/settings/ownTables/OwnSections";

/** Settings > Integrations: this server's GitHub App, its installations and its webhook. */
export const Route = createFileRoute("/_console/settings/integrations")({
  component: IntegrationsByServer,
});
