import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { PublishedPortsPage } from "../../../features/network/PublishedPortsPage";
import { validateNewSearch } from "../../../features/network/search";

/** What is published through nginx for what is not a web page: the first section of Network (it was Domains' in 4.1). `?new` opens the dialog that opens a port. */
export const Route = createFileRoute("/_console/network/")({
  validateSearch: validateNewSearch,
  component: PublishedRoute,
});

function PublishedRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <PublishedPortsPage opening={search.new === true} onOpened={() => void navigate({ search: {}, ...inPlace({ replace: true }) })} />;
}
