import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { ProjectsPage } from "../../../features/teams/ProjectsPage";
import { validateProjectsSearch } from "../../../features/teams/search";

/** Every project the operator works with. The search box is a search param: `/projects?q=web`. */
export const Route = createFileRoute("/_console/projects/")({
  validateSearch: validateProjectsSearch,
  component: ProjectsRoute,
});

function ProjectsRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <ProjectsPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
