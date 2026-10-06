import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { validateTeamsSearch } from "../../../features/teams/search";
import { TeamsPage } from "../../../features/teams/TeamsPage";

/** The teams the operator sees. The search box is a search param: `/teams?q=acme`. */
export const Route = createFileRoute("/_console/teams/")({
  validateSearch: validateTeamsSearch,
  component: TeamsRoute,
});

function TeamsRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <TeamsPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
