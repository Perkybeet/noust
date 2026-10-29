import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * The engines were a tab of Databases in 4.0. In 4.1 they are database servers, a section of their own: the
 * old address keeps working and opens it (app/nav.ts, REDIRECTS).
 */
export const Route = createFileRoute("/_console/databases/engines")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/databases/servers", replace: true });
  },
});
