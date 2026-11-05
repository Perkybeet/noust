import { createFileRoute, redirect } from "@tanstack/react-router";

/** 4.2: General listed what the application is; how it runs is now the page Resources and running. */
export const Route = createFileRoute("/_console/apps/$domain/settings/")({
  beforeLoad: ({ params }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/apps/$domain/resources", params, replace: true });
  },
});
