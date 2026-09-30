import { createFileRoute } from "@tanstack/react-router";

import { validateFleetSearch } from "../../../features/fleet/filters";
import { CertificatesTab } from "../../../features/fleet/CertificatesTab";

/** Fleet > Certificates: every certificate of every server, the soonest to expire first. */
export const Route = createFileRoute("/_console/fleet/certificates")({
  validateSearch: validateFleetSearch,
  component: FleetView,
});

function FleetView() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <CertificatesTab
      search={{ ...(search.q !== undefined ? { q: search.q } : {}), ...(search.server !== undefined ? { server: search.server } : {}), ...(search.state !== undefined ? { state: search.state } : {}) }}
      onSearchChange={(next, options) => void navigate({ search: next, replace: options?.replace ?? false })}
    />
  );
}
