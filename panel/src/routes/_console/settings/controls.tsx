import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * Settings > Controls: the compliance frameworks and the security profile. Until its page lands
 * (carved out of Settings > Security) it opens the screen that holds them today (app/nav.ts, INTERIM).
 */
export const Route = createFileRoute("/_console/settings/controls")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/settings/security", replace: true });
  },
});
