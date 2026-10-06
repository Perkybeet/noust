import { createFileRoute, redirect } from "@tanstack/react-router";

import { sessionQuery } from "../api/queries/auth";
import { FramedRoutePending } from "../app/RoutePending";
import { LoginPage } from "../features/auth/LoginPage";
import { safeNext } from "../features/auth/session";
import { ssoErrorOf, ssoTicketOf } from "../features/auth/sso";

export interface LoginSearch {
  next?: string;
  reason?: "expired";
  /** `token` opens on emergency access. */
  with?: "token";
  /** The ticket an identity provider's answer left: collected once, then taken out of the address. */
  sso?: string;
  /** Why a sign-in through an identity provider did not finish: one word the server chose. */
  sso_error?: string;
}

export const Route = createFileRoute("/login")({
  validateSearch: (search: Record<string, unknown>): LoginSearch => ({
    ...(typeof search["next"] === "string" ? { next: search["next"] } : {}),
    ...(search["reason"] === "expired" ? { reason: "expired" as const } : {}),
    ...(search["with"] === "token" ? { with: "token" as const } : {}),
    ...(typeof search["sso"] === "string" ? { sso: search["sso"] } : {}),
    ...(typeof search["sso_error"] === "string" ? { sso_error: search["sso_error"] } : {}),
  }),
  beforeLoad: async ({ context, search }) => {
    // An unreachable server is shown on the page itself, with the form still usable.
    const session = await context.queryClient.query(sessionQuery()).catch(() => null);
    if (session?.authenticated) {
      // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
      throw redirect({ href: safeNext(search.next), replace: true });
    }
  },
  component: LoginRoute,
  // The session is read before the page: past a second, the page's frame and a skeleton say so.
  pendingComponent: FramedRoutePending,
});

function LoginRoute() {
  const { next, reason, with: method, sso, sso_error: reasonWord } = Route.useSearch();
  // Whatever the address says is checked here, where it is used: a ticket is one value of a
  // known shape and a reason is one of the server's words, and nothing else is read as either.
  const ticket = ssoTicketOf(sso);
  const ssoError = ssoErrorOf(reasonWord);
  return (
    <LoginPage
      next={safeNext(next)}
      expired={reason === "expired"}
      {...(method === "token" ? { initialMode: "token" as const } : {})}
      {...(ticket !== undefined ? { sso: ticket } : {})}
      {...(ssoError !== undefined ? { ssoError } : {})}
    />
  );
}
