import { createFileRoute } from "@tanstack/react-router";

import { IntegrationsSettings } from "../../../features/settings/IntegrationsSettings";

/** Settings > Integrations: this server's GitHub App, its installations and its webhook. */
export const Route = createFileRoute("/_console/settings/integrations")({
  component: IntegrationsSettings,
});
