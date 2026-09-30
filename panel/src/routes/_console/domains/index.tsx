import { createFileRoute, redirect } from "@tanstack/react-router";

import { CertificatesPage } from "../../../features/domains/CertificatesPage";
import { validateCertificatesSearch } from "../../../features/domains/search";

/**
 * Every certificate on the machine, the first of the Domains and certificates tabs. Its filter
 * is a search param (`/domains?q=example.com`, the link a health report or an alert opens);
 * 3.0's `?tab=sites` goes to the sites' own address.
 */
export const Route = createFileRoute("/_console/domains/")({
  validateSearch: validateCertificatesSearch,
  beforeLoad: ({ search }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    if (search.tab === "sites") throw redirect({ to: "/domains/sites", replace: true });
  },
  component: CertificatesRoute,
});

function CertificatesRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <CertificatesPage search={search} onSearchChange={(next, options) => void navigate({ search: next, replace: options?.replace ?? false })} />
  );
}
