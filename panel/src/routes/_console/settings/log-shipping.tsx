import { createFileRoute } from "@tanstack/react-router";

import { LogShippingSettings } from "../../../features/settings/LogShippingSettings";

/** Settings > Log shipping: where the logs of applications go besides the journal. */
export const Route = createFileRoute("/_console/settings/log-shipping")({
  component: LogShippingSettings,
});
