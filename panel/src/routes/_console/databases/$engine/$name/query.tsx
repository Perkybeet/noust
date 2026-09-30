import { createFileRoute } from "@tanstack/react-router";

import { QueryTab } from "../../../../../features/databases/query/QueryTab";

export const Route = createFileRoute("/_console/databases/$engine/$name/query")({
  component: DatabaseQueryRoute,
});

function DatabaseQueryRoute() {
  const { engine, name } = Route.useParams();
  return <QueryTab engine={engine} name={name} />;
}
