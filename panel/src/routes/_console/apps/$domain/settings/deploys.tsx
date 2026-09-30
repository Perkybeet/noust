import { createFileRoute } from "@tanstack/react-router";

import { DeploysSettings } from "../../../../../features/app/settings/DeploysSettings";

/** An application's settings, Deploys: instant rollback, the startup check, zero-downtime deploys. */
export const Route = createFileRoute("/_console/apps/$domain/settings/deploys")({
  component: DeploysSettings,
});
