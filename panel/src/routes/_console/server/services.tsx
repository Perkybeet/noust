import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { ServicesPage } from "../../../features/services/ServicesPage";
import { validateServicesSearch } from "../../../features/services/data";

/** The services tab of the server: `/server/services?q=worker&all=1&state=failed`. */
export const Route = createFileRoute("/_console/server/services")({
  validateSearch: validateServicesSearch,
  component: ServicesRoute,
});

function ServicesRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <ServicesPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />
  );
}
