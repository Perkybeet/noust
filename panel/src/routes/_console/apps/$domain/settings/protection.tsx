import { createFileRoute } from "@tanstack/react-router";

import { ProtectionSettings } from "../../../../../features/app/settings/ProtectionSettings";

/** An application's settings, Protection: limits, response headers, HTTP/3, the firewall and client certificates. */
export const Route = createFileRoute("/_console/apps/$domain/settings/protection")({
  component: ProtectionSettings,
});
