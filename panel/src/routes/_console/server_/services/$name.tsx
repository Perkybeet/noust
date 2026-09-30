import { createFileRoute } from "@tanstack/react-router";

import { ServiceLayout } from "../../../../features/services/ServiceLayout";

/**
 * One service: its header and tabs. Not nested in the Server tabs: a service is a resource of
 * its own (T2), reached from the Services tab, with the server in its breadcrumbs.
 */
export const Route = createFileRoute("/_console/server_/services/$name")({
  component: ServiceRoute,
});

function ServiceRoute() {
  const { name } = Route.useParams();
  return <ServiceLayout key={name} name={name} />;
}
