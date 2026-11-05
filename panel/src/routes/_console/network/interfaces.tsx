import { createFileRoute } from "@tanstack/react-router";

import { InterfacesPage } from "../../../features/network/InterfacesPage";

/** Network > interfaces: the machine's network cards (it was a `?view=` of the Server's Network and ports in 4.1). */
export const Route = createFileRoute("/_console/network/interfaces")({
  component: InterfacesPage,
});
