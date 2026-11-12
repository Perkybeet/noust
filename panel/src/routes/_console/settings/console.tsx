import { createFileRoute } from "@tanstack/react-router";

import { ConsoleAddress } from "../../../features/settings/console/ConsoleAddress";

/** Settings > Console address: where the links of the notices open. Publishing a machine's console on a name of its own is Server > Console. */
export const Route = createFileRoute("/_console/settings/console")({
  component: ConsoleAddress,
});
