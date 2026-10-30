import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../../app/searchNavigation";
import { validateZoneSearch } from "../../../../../features/domains/noustdns/zoneSearch";
import { ZonePage } from "../../../../../features/domains/noustdns/ZonePage";

/** One zone of Noust DNS: its records, its DNSSEC and the steps to delegate the domain to it. */
export const Route = createFileRoute("/_console/domains/dns/zones/$zone")({
  validateSearch: validateZoneSearch,
  component: ZoneRoute,
});

function ZoneRoute() {
  const { zone } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  // A section of the page it is on: written in place, and the records, which are the first, are the address without a view.
  return <ZonePage zone={zone} view={search.view ?? "records"} onViewChange={(view) => void navigate({ search: view === "records" ? {} : { view }, ...inPlace({ replace: true }) })} />;
}
