import { createFileRoute } from "@tanstack/react-router";

import { UsersTab } from "../../../../../features/databases/users/UsersTab";

export const Route = createFileRoute("/_console/databases/$engine/$name/users")({
  component: DatabaseUsersRoute,
});

function DatabaseUsersRoute() {
  const { engine, name } = Route.useParams();
  return <UsersTab engine={engine} name={name} />;
}
