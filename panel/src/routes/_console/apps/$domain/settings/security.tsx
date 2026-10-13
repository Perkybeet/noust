import { createFileRoute } from "@tanstack/react-router";

import { SecuritySettings } from "../../../../../features/app/settings/SecuritySettings";

/** An application's settings, Unit security: how much of systemd's sandbox its process runs in. */
export const Route = createFileRoute("/_console/apps/$domain/settings/security")({
  component: SecuritySettings,
});
