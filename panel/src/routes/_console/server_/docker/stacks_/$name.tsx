import { createFileRoute } from "@tanstack/react-router";

import { StackPage } from "../../../../../features/docker/StackPage";

/** One stack, opened from the list of stacks: its containers, its document and what it touches. */
export const Route = createFileRoute("/_console/server_/docker/stacks_/$name")({
  component: StackRoute,
});

function StackRoute() {
  const { name } = Route.useParams();
  return <StackPage key={name} name={name} />;
}
