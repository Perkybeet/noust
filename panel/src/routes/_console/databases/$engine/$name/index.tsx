import { createFileRoute } from "@tanstack/react-router";

import { OverviewTab } from "../../../../../features/databases/overview/OverviewTab";

export const Route = createFileRoute("/_console/databases/$engine/$name/")({
  component: DatabaseOverviewRoute,
});

function DatabaseOverviewRoute() {
  const { engine, name } = Route.useParams();
  return <OverviewTab engine={engine} name={name} />;
}
