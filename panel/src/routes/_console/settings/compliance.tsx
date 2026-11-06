import { createFileRoute } from "@tanstack/react-router";

import { ComplianceSettings } from "../../../features/settings/compliance/ComplianceSettings";

/** Settings > Compliance: the frameworks this server can hold itself to, how the ones that are on stand, and the controls they are made of. */
export const Route = createFileRoute("/_console/settings/compliance")({
  component: ComplianceSettings,
});
