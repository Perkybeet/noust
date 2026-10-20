import { createFileRoute, redirect } from "@tanstack/react-router";

import { validateTeamsSearch } from "../../../features/teams/search";

/** The list of teams moved under Projects in 4.0: the old address keeps working, filters and all. */
export const Route = createFileRoute("/_console/teams/")({
  validateSearch: validateTeamsSearch,
  beforeLoad: ({ search }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/projects/teams", search, replace: true });
  },
});
