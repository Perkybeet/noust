import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { ApprovalsPage } from "../../../features/approvals/ApprovalsPage";
import { validateApprovalsSearch } from "../../../features/approvals/data";

/** Settings > Approvals: the calls that wait for a second person. */
export const Route = createFileRoute("/_console/settings/approvals")({
  validateSearch: validateApprovalsSearch,
  component: ApprovalsRoute,
});

function ApprovalsRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <ApprovalsPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
