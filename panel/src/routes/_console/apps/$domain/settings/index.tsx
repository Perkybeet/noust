import { createFileRoute } from "@tanstack/react-router";

import { GeneralSettings } from "../../../../../features/app/settings/GeneralSettings";

/** An application's settings, General: what the app is and how it runs. */
export const Route = createFileRoute("/_console/apps/$domain/settings/")({
  component: GeneralSettings,
});
