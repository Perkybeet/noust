import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * The operator's own account: profile and password. Until its page lands (it is carved out of
 * Settings > Security), the address opens the screen that holds those today, so the route is
 * never an empty page (app/nav.ts, INTERIM).
 */
export const Route = createFileRoute("/_console/account/")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/settings/security", replace: true });
  },
});
