import { createFileRoute } from "@tanstack/react-router";

import { ConnectTab } from "../../../../../features/databases/connect/ConnectTab";

export const Route = createFileRoute("/_console/databases/$engine/$name/connect")({
  component: DatabaseConnectRoute,
});

function DatabaseConnectRoute() {
  const { engine, name } = Route.useParams();
  return <ConnectTab engine={engine} name={name} />;
}
