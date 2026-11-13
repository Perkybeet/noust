import { createFileRoute } from "@tanstack/react-router";

import { ContainerPage } from "../../../../../features/docker/ContainerPage";
import { validateContainerSearch } from "../../../../../features/docker/search";

/** One container from the inside, opened from a list: its state and why, its logs as they are written, what it uses and what it touches. */
export const Route = createFileRoute("/_console/server_/docker/containers_/$ref")({
  validateSearch: validateContainerSearch,
  component: ContainerRoute,
});

function ContainerRoute() {
  const { ref } = Route.useParams();
  const search = Route.useSearch();
  return <ContainerPage key={ref} name={ref} console={search.console === true} />;
}
