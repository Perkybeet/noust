import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { PolicyPage } from "../../../features/approvals/policy/PolicyPage";
import { validatePolicySearch } from "../../../features/approvals/policy/search";

/** Settings > Approval policy: what a token and the AI assistant do with a call that changes something. */
export const Route = createFileRoute("/_console/settings/approval-policy")({
  validateSearch: validatePolicySearch,
  component: PolicyRoute,
});

function PolicyRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <PolicyPage search={search} onSearch={(next) => void navigate({ search: next, ...inPlace({}) })} />;
}
