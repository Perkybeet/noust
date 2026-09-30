import { createFileRoute, redirect } from "@tanstack/react-router";

import { sessionQuery } from "../api/queries/auth";
import { FirstAccountPage } from "../features/auth/FirstAccountPage";
import { safeNext } from "../features/auth/session";

export interface SetupSearch {
  next?: string;
}

/** The first accounts of a server that has none, for whoever holds the access token. */
export const Route = createFileRoute("/setup")({
  validateSearch: (search: Record<string, unknown>): SetupSearch => (typeof search["next"] === "string" ? { next: search["next"] } : {}),
  beforeLoad: async ({ context, location }) => {
    const session = await context.queryClient.query(sessionQuery());
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    if (!session.authenticated) throw redirect({ to: "/login", search: { next: location.href } });
  },
  component: SetupRoute,
});

function SetupRoute() {
  const { next } = Route.useSearch();
  return <FirstAccountPage next={safeNext(next)} />;
}
