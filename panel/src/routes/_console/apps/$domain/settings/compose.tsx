import { createFileRoute } from "@tanstack/react-router";

import { ComposeStackSettings } from "../../../../../features/app/settings/ComposeStackSettings";

/** An application's settings, Compose: the stack's file, profiles and arguments, its networks and its registry accounts. */
export const Route = createFileRoute("/_console/apps/$domain/settings/compose")({
  component: ComposeStackSettings,
});
