import { createFileRoute, redirect } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { DomainsPage } from "../../../features/domains/DomainsPage";
import { validateCertificatesSearch } from "../../../features/domains/search";

/**
 * The names the server answers on, the first of the Domains sections. Its filter is a search param
 * (`/domains?q=example.com`, the link a health report or an alert opens: a name is a row here, with
 * its certificate beside it). 3.0's `?tab=sites` goes to the sites' own address and its
 * `?tab=certificates` to the certificates'.
 */
export const Route = createFileRoute("/_console/domains/")({
  validateSearch: validateCertificatesSearch,
  beforeLoad: ({ search }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    if (search.tab === "sites") throw redirect({ to: "/domains/sites", replace: true });
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    if (search.tab === "certificates") throw redirect({ to: "/domains/certificates", replace: true });
  },
  component: DomainsRoute,
});

function DomainsRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <DomainsPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
