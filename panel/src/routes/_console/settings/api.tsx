import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { ApiReference, validateApiReferenceSearch } from "../../../features/settings/api/ApiReference";

/** Settings > API reference: every route this server answers. Filters are search params: `/settings/api?tag=Jobs`. */
export const Route = createFileRoute("/_console/settings/api")({
  validateSearch: validateApiReferenceSearch,
  component: ApiReferenceRoute,
});

function ApiReferenceRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <ApiReference search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
