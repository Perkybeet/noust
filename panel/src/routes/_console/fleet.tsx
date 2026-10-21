import { createFileRoute } from "@tanstack/react-router";

import { inPlace } from "../../app/searchNavigation";
import { parseHubArea } from "../../features/central/central";
import type { HubArea } from "../../features/central/central";
import { FleetLayout } from "../../features/fleet/FleetLayout";

interface FleetLayoutSearch {
  /** A hub was asked for one of the pages it does not have, and opened the fleet instead. */
  hub?: HubArea | "overview";
  /** The "Add a server" flow is open: the palette and the old Settings > Servers address open it. */
  add?: true;
}

function validateSearch(search: Record<string, unknown>): FleetLayoutSearch {
  const hub = parseHubArea(search["hub"]);
  const add = search["add"] === true || search["add"] === "true" || search["add"] === 1;
  return { ...(hub === undefined ? {} : { hub }), ...(add ? { add: true as const } : {}) };
}

/** The fleet ("All servers"): its header and one view per URL, every server at once. */
export const Route = createFileRoute("/_console/fleet")({
  validateSearch,
  component: FleetRoute,
});

/** The search with the "Add a server" flow opened or closed, whatever else it carries. */
function withAdd<S extends { add?: true }>(search: S, adding: boolean): S {
  const next = { ...search };
  if (adding) next.add = true;
  else delete next.add;
  return next;
}

function FleetRoute() {
  const { hub, add } = Route.useSearch();
  const navigate = Route.useNavigate();
  return (
    <FleetLayout
      hub={hub}
      adding={add === true}
      onAddingChange={(adding) => void navigate({ search: (previous) => withAdd(previous, adding), ...inPlace({ replace: true }) })}
    />
  );
}
