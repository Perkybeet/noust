import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { NetworkPage } from "../../../features/server/network/NetworkPage";
import { validateNetworkSearch } from "../../../features/server/network/data";

/** Network and ports: who uses each port, the firewall, the ports published in front of a service, and the interfaces. */
export const Route = createFileRoute("/_console/server/network")({
  validateSearch: validateNetworkSearch,
  component: NetworkRoute,
});

function NetworkRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <NetworkPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
