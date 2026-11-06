import { createFileRoute } from "@tanstack/react-router";

import { SettingsTab } from "../../../../../features/databases/settings/SettingsTab";

export const Route = createFileRoute("/_console/databases/$engine/$name/settings")({
  component: DatabaseSettingsRoute,
});

function DatabaseSettingsRoute() {
  const { engine, name } = Route.useParams();
  return <SettingsTab engine={engine} name={name} />;
}
