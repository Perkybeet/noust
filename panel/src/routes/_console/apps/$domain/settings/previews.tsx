import { createFileRoute } from "@tanstack/react-router";

import { PreviewsSettings } from "../../../../../features/app/settings/PreviewsSection";

/** An application's settings, Previews: a copy of the app for each pull request. */
export const Route = createFileRoute("/_console/apps/$domain/settings/previews")({
  component: PreviewsSettings,
});
