import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../../app/searchNavigation";
import { ProjectPage } from "../../../../features/teams/ProjectPage";
import { validateProjectSearch } from "../../../../features/teams/search";

/** One project: its environments, and what lives in each. The environment is a search param. */
export const Route = createFileRoute("/_console/projects/$team/$project")({
  validateSearch: validateProjectSearch,
  component: ProjectRoute,
});

function ProjectRoute() {
  const { team, project } = Route.useParams();
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <ProjectPage key={`${team}/${project}`} team={team} project={project} search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
