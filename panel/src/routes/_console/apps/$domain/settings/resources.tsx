import { createFileRoute } from "@tanstack/react-router";

import { ResourcesSettings } from "../../../../../features/app/settings/ResourcesSettings";

/** An application's settings, Resources: the most it may use of the server. */
export const Route = createFileRoute("/_console/apps/$domain/settings/resources")({
  component: ResourcesSettings,
});
