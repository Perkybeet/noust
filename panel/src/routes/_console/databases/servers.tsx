import { createFileRoute } from "@tanstack/react-router";

import { ServersPage } from "../../../features/databases/ServersPage";

/** The database servers: where the databases run, and the version each is on. `?engine=mariadb` keeps the servers of one engine. */
export const Route = createFileRoute("/_console/databases/servers")({
  validateSearch: (search: Record<string, unknown>): { engine?: string } => {
    const engine = typeof search["engine"] === "string" && /^[a-z]+(@[a-z0-9_.-]+)?$/.test(search["engine"]) ? search["engine"] : undefined;
    return engine === undefined ? {} : { engine };
  },
  component: ServersRoute,
});

function ServersRoute() {
  const { engine } = Route.useSearch();
  return <ServersPage engine={engine} />;
}
