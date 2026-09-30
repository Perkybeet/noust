import { createFileRoute } from "@tanstack/react-router";

import { BuildsSettings } from "../../../../../features/app/settings/BuildsSettings";

/** An application's settings, Builds: whether its builds run in the sandbox. */
export const Route = createFileRoute("/_console/apps/$domain/settings/builds")({
  component: BuildsSettings,
});
