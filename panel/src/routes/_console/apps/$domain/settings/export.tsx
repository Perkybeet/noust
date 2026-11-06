import { createFileRoute, redirect } from "@tanstack/react-router";

/** 4.2: the settings of an application are the pages of its column; this subsection is now in export. */
export const Route = createFileRoute("/_console/apps/$domain/settings/export")({
  beforeLoad: ({ params }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/apps/$domain/export", params, replace: true });
  },
});
