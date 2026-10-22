import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { inPlace } from "../../app/searchNavigation";
import { parseHubArea } from "../../features/central/central";
import type { HubArea } from "../../features/central/central";
import { FleetLayout } from "../../features/fleet/FleetLayout";
import { useOnArrival } from "../../lib/useArrival";

interface FleetLayoutSearch {
  /** A hub was asked for one of the pages it does not have, and opened the fleet instead. */
  hub?: HubArea | "overview";
  /** A link that asks for the "Add a server" flow once: the palette, and the old Settings > Servers address. */
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

/** The search without the one-shot `add`, whatever else it carries. */
function spent<S extends { add?: true }>(search: S): S {
  const next = { ...search };
  delete next.add;
  return next;
}

function FleetRoute() {
  const { hub, add } = Route.useSearch();
  // The layout wraps every view of the fleet: moving within the current page, not to the layout's own.
  const navigate = useNavigate();
  const [adding, setAdding] = useState(false);
  // `?add` is asked once: the flow opens and the address goes back to the page it is on.
  useOnArrival(add === true, () => {
    setAdding(true);
    void navigate({ to: ".", search: (previous: FleetLayoutSearch) => spent(previous), ...inPlace({ replace: true }) });
  });
  return <FleetLayout hub={hub} adding={adding} onAddingChange={setAdding} />;
}
