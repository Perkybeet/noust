import { createFileRoute } from "@tanstack/react-router";

import { DatabasesPage } from "../../../features/databases/DatabasesPage";
import { validateDatabasesSearch } from "../../../features/databases/filters";

/** Every database on the machine. Filters are search params: `/databases?engine=postgresql`. */
export const Route = createFileRoute("/_console/databases/")({
  validateSearch: validateDatabasesSearch,
  component: DatabasesRoute,
});

function DatabasesRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <DatabasesPage search={search} onSearchChange={(next, options) => void navigate({ search: next, replace: options?.replace ?? false })} />;
}
