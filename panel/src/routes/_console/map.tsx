import { createFileRoute, redirect } from "@tanstack/react-router";

/**
 * The map was Home's second view in 4.0 and 4.1. Home is a panel of widgets now and has no second view, so the
 * old address keeps working and opens Home (app/nav.ts, REDIRECTS). `noust map` and `GET /api/map` stay as they were.
 */
export const Route = createFileRoute("/_console/map")({
  beforeLoad: () => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/", replace: true });
  },
});
