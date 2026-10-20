import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * Sign-in security: the authenticator, passkeys and sessions of the signed-in account. Until
 * its page lands it opens the screen that holds them today (app/nav.ts, INTERIM).
 */
export const Route = createFileRoute("/_console/account/security")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/settings/security", replace: true });
  },
});
