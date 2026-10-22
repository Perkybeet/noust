import { createFileRoute, redirect } from "@tanstack/react-router";

import { validateFleetSearch } from "../../../features/fleet/filters";
import type { FleetSearch } from "../../../features/fleet/filters";

interface ServersSearch extends FleetSearch {
  /** The "Add a server" flow was open: the Fleet page links straight to it. */
  add?: true;
}

function validateSearch(search: Record<string, unknown>): ServersSearch {
  const add = search["add"] === true || search["add"] === "true" || search["add"] === 1;
  return { ...validateFleetSearch(search), ...(add ? { add: true as const } : {}) };
}

/**
 * Settings > Servers was a second list of the servers; the fleet's is the one list now, and the
 * old address (with its filters, and the "Add a server" flow it opened) goes there.
 */
export const Route = createFileRoute("/_console/settings/servers")({
  validateSearch,
  beforeLoad: ({ search }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/fleet/servers", search, replace: true });
  },
});
