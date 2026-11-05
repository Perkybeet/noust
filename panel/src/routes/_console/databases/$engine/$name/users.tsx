import { createFileRoute, redirect } from "@tanstack/react-router";

/** A database's Users are its Access (the CLI and the API always called it that): the old address keeps working. */
export const Route = createFileRoute("/_console/databases/$engine/$name/users")({
  beforeLoad: ({ params }) => {
    // eslint-disable-next-line @typescript-eslint/only-throw-error -- the router's redirect protocol
    throw redirect({ to: "/databases/$engine/$name/access", params, replace: true });
  },
});
