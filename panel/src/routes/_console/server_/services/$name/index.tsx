import { createFileRoute } from "@tanstack/react-router";

import { ServiceOverviewTab } from "../../../../../features/services/ServiceOverviewTab";

export const Route = createFileRoute("/_console/server_/services/$name/")({
  component: ServiceOverviewRoute,
});

function ServiceOverviewRoute() {
  const { name } = Route.useParams();
  return <ServiceOverviewTab name={name} />;
}
