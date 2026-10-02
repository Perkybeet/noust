import { createFileRoute } from "@tanstack/react-router";

import { HooksSettings } from "../../../../../features/app/settings/HooksSettings";

/** An application's settings, Deploy hooks: what runs before and after a deploy, and where it comes from. */
export const Route = createFileRoute("/_console/apps/$domain/settings/hooks")({
  component: HooksSettings,
});
