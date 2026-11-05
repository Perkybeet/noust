import { createFileRoute, redirect } from "@tanstack/react-router";

import { validateEnginesSearch } from "../../../features/databases/search";

/**
 * 4.1's database servers are the Engines of 4.2: the old address, with the engine it asked to look at, opens them
 * (app/redirects.ts).
 */
export const Route = createFileRoute("/_console/databases/servers")({
  validateSearch: validateEnginesSearch,
  beforeLoad: ({ search }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/databases/engines", search, replace: true });
  },
});
