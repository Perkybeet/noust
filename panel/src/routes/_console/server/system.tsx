import { createFileRoute, redirect } from "@tanstack/react-router";

import { validateSystemSearch } from "../../../features/server/system/data";

/**
 * System and updates was one section with four views in 4.1 (`?view=`); each is a section of the server now, and the old
 * address goes to the one it asked for (what it opened on without a view, the updates, is still where it goes). The
 * interfaces were a view of it in 4.0: they are Network's.
 */
export const Route = createFileRoute("/_console/server/system")({
  validateSearch: validateSystemSearch,
  beforeLoad: ({ search }) => {
    const to = search.view === undefined ? "/server/updates" : ({ network: "/network/interfaces", machine: "/server/machine", processes: "/server/processes", monitor: "/server/monitor" } as const)[search.view];
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to, replace: true });
  },
});
