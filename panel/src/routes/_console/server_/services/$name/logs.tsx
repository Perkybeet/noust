import { createFileRoute } from "@tanstack/react-router";

import { ServiceLogsTab } from "../../../../../features/services/ServiceLogsTab";

export const Route = createFileRoute("/_console/server_/services/$name/logs")({
  component: ServiceLogsRoute,
});

function ServiceLogsRoute() {
  const { name } = Route.useParams();
  return <ServiceLogsTab name={name} />;
}
