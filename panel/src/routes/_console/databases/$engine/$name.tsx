import { createFileRoute } from "@tanstack/react-router";

import { DatabaseLayout } from "../../../../features/databases/DatabaseLayout";

/** One database: its header and the tabs that divide it. Each tab is a URL. */
export const Route = createFileRoute("/_console/databases/$engine/$name")({
  component: DatabaseRoute,
});

function DatabaseRoute() {
  const { engine, name } = Route.useParams();
  // Keyed: another database is another page, with none of this one's job or dialogs.
  return <DatabaseLayout key={`${engine}/${name}`} engine={engine} name={name} />;
}
