import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { SecurityTab } from "../../../features/server/security/SecurityTab";
import { validateSecuritySearch } from "../../../features/server/security/data";

/** The hardening checks and the views behind them: `/server/security?view=firewall`. */
export const Route = createFileRoute("/_console/server/security")({
  validateSearch: validateSecuritySearch,
  component: SecurityRoute,
});

function SecurityRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <SecurityTab view={search.view ?? "checks"} onViewChange={(view) => void navigate({ search: view === "checks" ? {} : { view }, ...inPlace({ replace: true }) })} />;
}
