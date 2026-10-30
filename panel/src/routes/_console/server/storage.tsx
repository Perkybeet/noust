import { createFileRoute, redirect } from "@tanstack/react-router";

/** Storage is Files in 4.1: the old address goes there. */
export const Route = createFileRoute("/_console/server/storage")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/server/files", replace: true });
  },
});
