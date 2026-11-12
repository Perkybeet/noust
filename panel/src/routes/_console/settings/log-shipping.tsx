import { createFileRoute } from "@tanstack/react-router";

import { LogShippingByServer } from "../../../features/settings/ownTables/OwnSections";

/** Settings > Log shipping: where the logs of applications go besides the journal, and how long the journal keeps them. */
export const Route = createFileRoute("/_console/settings/log-shipping")({
  component: LogShippingByServer,
});
