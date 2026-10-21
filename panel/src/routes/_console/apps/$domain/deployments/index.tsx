import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../../app/searchNavigation";
import { DeploymentsTab } from "../../../../../features/app/deployments/DeploymentsTab";
import { validateDeploymentsSearch } from "../../../../../features/app/deployments/filters";

/** An app's deployments. The filters are search params: `?state=failed&branch=main&q=cart`. */
export const Route = createFileRoute("/_console/apps/$domain/deployments/")({
  validateSearch: validateDeploymentsSearch,
  component: AppDeploymentsTab,
});

function AppDeploymentsTab() {
  const { domain } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <DeploymentsTab domain={domain} search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
