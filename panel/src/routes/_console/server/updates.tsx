import { createFileRoute, redirect } from "@tanstack/react-router";

/** System updates joined the System section in 4.1, where they are its first view: the old address goes there. */
export const Route = createFileRoute("/_console/server/updates")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/server/system", replace: true });
  },
});
