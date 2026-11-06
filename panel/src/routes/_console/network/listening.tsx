import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { ListeningPage } from "../../../features/network/ListeningPage";
import { validateNetworkSearch } from "../../../features/server/network/data";

/** Network > listening ports: the one table of who listens on a port; `?q=5432` is the link the other pages open it with. */
export const Route = createFileRoute("/_console/network/listening")({
  validateSearch: validateNetworkSearch,
  component: ListeningRoute,
});

function ListeningRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <ListeningPage query={search.q ?? ""} onQueryChange={(q) => void navigate({ search: q.trim() === "" ? {} : { q }, ...inPlace({ replace: true }) })} />;
}
