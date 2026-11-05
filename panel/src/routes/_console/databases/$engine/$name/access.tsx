import { createFileRoute } from "@tanstack/react-router";

import { AccessTab } from "../../../../../features/databases/access/AccessTab";

export const Route = createFileRoute("/_console/databases/$engine/$name/access")({
  component: DatabaseAccessRoute,
});

function DatabaseAccessRoute() {
  const { engine, name } = Route.useParams();
  return <AccessTab engine={engine} name={name} />;
}
