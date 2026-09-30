import { createFileRoute } from "@tanstack/react-router";

import { UnitFilePage } from "../../../../../features/services/UnitFilePage";

/** A service's unit file (T6): the editor alone, with the service in its breadcrumbs. */
export const Route = createFileRoute("/_console/server_/services/$name_/unit")({
  component: UnitFileRoute,
});

function UnitFileRoute() {
  const { name } = Route.useParams();
  return <UnitFilePage key={name} name={name} />;
}
