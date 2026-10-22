import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * Settings > Security was split in 4.0: the person's own sign-in security is the account's
 * (`/account/security`), the compliance frameworks and the security profile are Settings >
 * Controls. The old address keeps working and opens the account's, which is what most links to
 * it were about (app/nav.ts, REDIRECTS).
 */
export const Route = createFileRoute("/_console/settings/security")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/account/security", replace: true });
  },
});
