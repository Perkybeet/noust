import { createFileRoute } from "@tanstack/react-router";

import { DeleteTab } from "../../../../../features/databases/delete/DeleteTab";

export const Route = createFileRoute("/_console/databases/$engine/$name/delete")({
  component: DatabaseDeleteRoute,
});

function DatabaseDeleteRoute() {
  const { engine, name } = Route.useParams();
  return <DeleteTab engine={engine} name={name} />;
}
