import { createFileRoute } from "@tanstack/react-router";

import { BuildSettings } from "../../../../../features/app/settings/BuildSettings";

/** An application's settings, Build commands: what installs, builds and starts it, and where the project lives. */
export const Route = createFileRoute("/_console/apps/$domain/settings/build")({
  component: BuildSettings,
});
