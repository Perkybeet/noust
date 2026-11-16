import { createFileRoute, redirect } from "@tanstack/react-router";

import { sessionQuery } from "../api/queries/auth";
import { FramedRoutePending } from "../app/RoutePending";
import { FirstAccountPage } from "../features/auth/FirstAccountPage";
import { safeNext } from "../features/auth/session";

export interface SetupSearch {
  next?: string;
  /** `protect` opens on the second factor of an account that exists: the offer that comes back a week after "Set up later". */
  step?: "protect";
}

/** The first accounts of a server that has none, for whoever holds the access token; and, with `?step=protect`, an account's second factor. */
export const Route = createFileRoute("/setup")({
  validateSearch: (search: Record<string, unknown>): SetupSearch => ({
    ...(typeof search["next"] === "string" ? { next: search["next"] } : {}),
    ...(search["step"] === "protect" ? { step: "protect" as const } : {}),
  }),
  beforeLoad: async ({ context, location, search }) => {
    const session = await context.queryClient.query(sessionQuery());
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    if (!session.authenticated) throw redirect({ to: "/login", search: { next: location.href } });
    // The step is for an account that is not protected: anyone else has nothing to do on it, and a link that is stale does not strand them.
    const account = session.account;
    if (search.step === "protect" && (account === null || account === undefined || account.mfa_enabled || session.totp_enabled)) {
      // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
      throw redirect({ href: safeNext(search.next), replace: true });
    }
  },
  component: SetupRoute,
  // The session is read before the page: past a second, the page's frame and a skeleton say so.
  pendingComponent: FramedRoutePending,
});

function SetupRoute() {
  const { next, step } = Route.useSearch();
  return <FirstAccountPage next={safeNext(next)} {...(step !== undefined ? { start: step } : {})} />;
}
