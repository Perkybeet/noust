import { createFileRoute } from "@tanstack/react-router";

import { ServersSettings } from "../../../features/settings/servers/ServersSettings";

interface ServersSearch {
  /** The "Add a server" flow is open: the Fleet page links straight to it. */
  add?: true;
}

function validateSearch(search: Record<string, unknown>): ServersSearch {
  return search["add"] === true || search["add"] === "true" || search["add"] === 1 ? { add: true } : {};
}

/** Settings > Servers: the servers this central manages. */
export const Route = createFileRoute("/_console/settings/servers")({
  validateSearch,
  component: ServersRoute,
});

function ServersRoute() {
  const { add } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <ServersSettings
      adding={add === true}
      onAddingChange={(adding) => void navigate({ search: adding ? { add: true } : {}, replace: true })}
    />
  );
}
