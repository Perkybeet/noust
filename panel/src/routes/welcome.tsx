import { createFileRoute, redirect } from "@tanstack/react-router";

import { sessionQuery } from "../api/queries/auth";
import { GatePage } from "../features/auth/GatePage";
import { safeNext } from "../features/auth/session";
import { pendingChecks } from "../features/auth/SessionGate";

export interface WelcomeSearch {
  next?: string;
}

/**
 * What an account does right after signing in, before the console: enrol a second factor,
 * accept the usage notice. Nothing pending, nothing to do here.
 */
export const Route = createFileRoute("/welcome")({
  validateSearch: (search: Record<string, unknown>): WelcomeSearch => (typeof search["next"] === "string" ? { next: search["next"] } : {}),
  beforeLoad: async ({ context, search }) => {
    const session = await context.queryClient.query({ ...sessionQuery(), staleTime: 0 });
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    if (!session.authenticated) throw redirect({ to: "/login", search: search.next === undefined ? {} : { next: search.next } });
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    if (!pendingChecks(session)) throw redirect({ href: safeNext(search.next), replace: true });
  },
  component: WelcomeRoute,
});

function WelcomeRoute() {
  const { next } = Route.useSearch();
  return <GatePage next={safeNext(next)} />;
}
