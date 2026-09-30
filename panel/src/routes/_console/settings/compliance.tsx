import { createFileRoute } from "@tanstack/react-router";

import { CompliancePage } from "../../../features/audit/CompliancePage";

/** Settings > Compliance: this server against the ENS category MEDIUM profile. */
export const Route = createFileRoute("/_console/settings/compliance")({
  component: CompliancePage,
});
