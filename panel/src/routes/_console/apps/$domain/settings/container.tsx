import { createFileRoute } from "@tanstack/react-router";

import { ContainerSettings } from "../../../../../features/app/settings/ContainerSettings";

/** An application's settings, Container: how its image is made and how it runs. */
export const Route = createFileRoute("/_console/apps/$domain/settings/container")({
  component: ContainerSettings,
});
