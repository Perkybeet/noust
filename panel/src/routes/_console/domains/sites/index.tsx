import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { SitesPage } from "../../../../features/domains/SitesPage";
import { validateSitesSearch } from "../../../../features/domains/search";

/** Every web server site on the machine. The filter is a search param: `/domains/sites?q=shop`. */
export const Route = createFileRoute("/_console/domains/sites/")({
  validateSearch: validateSitesSearch,
  component: SitesRoute,
});

function SitesRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <SitesPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
