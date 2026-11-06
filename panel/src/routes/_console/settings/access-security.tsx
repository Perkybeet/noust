import { createFileRoute } from "@tanstack/react-router";

import { AccessSecuritySettings } from "../../../features/settings/accessSecurity/AccessSecuritySettings";

/** Settings > Access security: how people get into the console (second factor, sessions, lockouts) and the token for when nothing else works. */
export const Route = createFileRoute("/_console/settings/access-security")({
  component: AccessSecuritySettings,
});
