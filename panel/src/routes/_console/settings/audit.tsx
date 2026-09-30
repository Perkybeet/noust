import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { AuditPage } from "../../../features/audit/AuditPage";
import { validateAuditSearch } from "../../../features/audit/data";

/** Settings > Audit log: every privileged action, and whether the chain holds. */
export const Route = createFileRoute("/_console/settings/audit")({
  validateSearch: validateAuditSearch,
  component: AuditRoute,
});

function AuditRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <AuditPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
