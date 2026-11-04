import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { useT } from "../../../i18n";
import { MachinePage } from "../../../features/server/MachinePage";
import { NetworkPage } from "../../../features/server/network/NetworkPage";
import { validateNetworkSearch } from "../../../features/server/network/data";

/** Network > listening: a view of what the machine listens on, which was a `?view=` of the Server's Network and ports in 4.1. */
export const Route = createFileRoute("/_console/network/listening")({
  validateSearch: validateNetworkSearch,
  component: ListeningRoute,
});

function ListeningRoute() {
  const t = useT();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <MachinePage title={t("serverPorts.views.ports")}>
      <NetworkPage view="ports" search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />
    </MachinePage>
  );
}
