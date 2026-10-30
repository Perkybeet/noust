import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { CertificatesPage } from "../../../features/domains/CertificatesPage";
import { validateCertificatesSearch } from "../../../features/domains/search";

/**
 * Every certificate on the machine, the soonest to expire first. Its filter is a search param
 * (`/domains/certificates?q=example.com`).
 */
export const Route = createFileRoute("/_console/domains/certificates")({
  validateSearch: validateCertificatesSearch,
  component: CertificatesRoute,
});

function CertificatesRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <CertificatesPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />
  );
}
