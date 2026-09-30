import { createFileRoute } from "@tanstack/react-router";

import { BackupsTab } from "../../../../../features/databases/backups/BackupsTab";

export const Route = createFileRoute("/_console/databases/$engine/$name/backups")({
  component: DatabaseBackupsRoute,
});

function DatabaseBackupsRoute() {
  const { engine, name } = Route.useParams();
  return <BackupsTab engine={engine} name={name} />;
}
