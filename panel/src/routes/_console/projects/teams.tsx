import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { validateTeamsSearch } from "../../../features/teams/search";
import { TeamsPage } from "../../../features/teams/TeamsPage";

/**
 * The teams the operator sees, the Teams tab of Projects. The search box is a search param:
 * `/projects/teams?q=acme`. `/teams` was this page until 4.0 and still opens it (app/nav.ts, REDIRECTS).
 */
export const Route = createFileRoute("/_console/projects/teams")({
  validateSearch: validateTeamsSearch,
  component: TeamsRoute,
});

function TeamsRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <TeamsPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
