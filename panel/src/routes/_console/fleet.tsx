import { createFileRoute, useNavigate } from "@tanstack/react-router";
import { useState } from "react";

import { inPlace } from "../../app/searchNavigation";
import { FleetLayout } from "../../features/fleet/FleetLayout";
import { nodesQuery } from "../../features/fleet/nodesList";
import { useOnArrival } from "../../lib/useArrival";

interface FleetLayoutSearch {
  /** A link that asks for the "Add a server" flow once: the palette, and the old Settings > Servers address. */
  add?: true;
}

function validateSearch(search: Record<string, unknown>): FleetLayoutSearch {
  // The `hub` an address of 4.2 carried (a hub that sent its pages here) is not read: the page it asked for says why it is not there.
  const add = search["add"] === true || search["add"] === "true" || search["add"] === 1;
  return add ? { add: true as const } : {};
}

/** The fleet ("All servers"): its header and one view per URL, every server at once. */
export const Route = createFileRoute("/_console/fleet")({
  validateSearch,
  // Whether the fleet has a server decides the whole frame: an empty one draws its first-use state instead of the views.
  // Known before the frame is drawn, the frame never draws a view and then swaps it for that state (on a cold load the
  // list answered after the first paint, and the swap moved the page). A list that fails is the frame's to say.
  // Only when nothing is cached: a later visit draws from what the shell already holds and lets it refresh behind.
  loader: async ({ context }) => {
    if (context.queryClient.getQueryData(nodesQuery().queryKey) !== undefined) return;
    await context.queryClient.query(nodesQuery()).catch(() => undefined);
  },
  component: FleetRoute,
});

/** The search without the one-shot `add`, whatever else it carries. */
function spent<S extends { add?: true }>(search: S): S {
  const next = { ...search };
  delete next.add;
  return next;
}

function FleetRoute() {
  const { add } = Route.useSearch();
  // The layout wraps every view of the fleet: moving within the current page, not to the layout's own.
  const navigate = useNavigate();
  const [adding, setAdding] = useState(false);
  // `?add` is asked once: the flow opens and the address goes back to the page it is on.
  useOnArrival(add === true, () => {
    setAdding(true);
    void navigate({ to: ".", search: (previous: FleetLayoutSearch) => spent(previous), ...inPlace({ replace: true }) });
  });
  return <FleetLayout adding={adding} onAddingChange={setAdding} />;
}
