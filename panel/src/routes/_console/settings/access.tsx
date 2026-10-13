import { createFileRoute } from "@tanstack/react-router";

import { AccessSettings } from "../../../features/settings/access/AccessSettings";

/** Settings > Roles and access: a role given where it is needed, and the roles of your own. */
export const Route = createFileRoute("/_console/settings/access")({
  component: AccessSettings,
});
