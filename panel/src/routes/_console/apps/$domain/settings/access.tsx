import { createFileRoute } from "@tanstack/react-router";

import { AccessSettings } from "../../../../../features/app/settings/AccessSettings";

/** An application's settings, Access: a password, address lists, limits and response headers. */
export const Route = createFileRoute("/_console/apps/$domain/settings/access")({
  component: AccessSettings,
});
