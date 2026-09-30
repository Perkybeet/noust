import { createFileRoute } from "@tanstack/react-router";

import { BackupsPage } from "../../../features/backups/BackupsPage";
import { validateCoverageSearch } from "../../../features/backups/coverage";

/**
 * Every application and its backups. The filters and the application whose backups are open
 * are search params: `/backups?domain=shop.example.com` opens that application's backups.
 */
export const Route = createFileRoute("/_console/backups/")({
  validateSearch: validateCoverageSearch,
  component: BackupsRoute,
});

function BackupsRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <BackupsPage search={search} onSearchChange={(next, options) => void navigate({ search: next, replace: options?.replace ?? false })} />
  );
}
