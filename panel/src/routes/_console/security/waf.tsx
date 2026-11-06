import { createFileRoute } from "@tanstack/react-router";

import { WafPage } from "../../../features/server/security/SecurityPages";

/** Security > web firewall: whether the server can run ModSecurity, and the level each site has it at. */
export const Route = createFileRoute("/_console/security/waf")({
  component: WafPage,
});
