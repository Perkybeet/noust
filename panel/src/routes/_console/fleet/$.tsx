import { createFileRoute } from "@tanstack/react-router";

import { FleetSectionPage } from "../../../features/fleet/FleetSectionPage";

/**
 * Any section of the console seen from "All servers" whose fleet page is not built yet: `/fleet/databases/engines` is the engines of
 * every server. The pages of the fleet that exist (`/fleet/apps`, `/fleet/backups`...) are routes of their own and win over this one.
 */
export const Route = createFileRoute("/_console/fleet/$")({
  component: FleetSectionRoute,
});

function FleetSectionRoute() {
  const { _splat } = Route.useParams();
  return <FleetSectionPage path={`/${_splat ?? ""}`} />;
}
