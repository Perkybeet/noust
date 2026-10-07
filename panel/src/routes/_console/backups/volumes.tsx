import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../../app/searchNavigation";
import { VolumesPage, validateVolumesSearch } from "../../../features/backups/volumes/VolumesPage";

/**
 * The data of each application, backed up on its own. The application whose backups are open is
 * a search param: `/backups/volumes?domain=shop.example.com`.
 */
export const Route = createFileRoute("/_console/backups/volumes")({
  validateSearch: validateVolumesSearch,
  component: VolumesRoute,
});

function VolumesRoute() {
  const search = Route.useSearch();
  const navigate = Route.useNavigate();
  return <VolumesPage search={search} onSearchChange={(next, options) => void navigate({ search: next, ...inPlace(options) })} />;
}
