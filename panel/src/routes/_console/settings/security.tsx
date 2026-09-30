import { createFileRoute } from "@tanstack/react-router";

import { SecuritySettings } from "../../../features/settings/SecuritySettings";

/** Settings > Security: the own account, its second factors and passkeys, sessions and the policy. */
export const Route = createFileRoute("/_console/settings/security")({
  component: SecuritySettings,
});
