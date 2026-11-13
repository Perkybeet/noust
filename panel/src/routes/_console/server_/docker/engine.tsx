import { createFileRoute } from "@tanstack/react-router";

import { EnginePage } from "../../../../features/docker/EnginePage";

/** Which Docker there is and what fits in it, said by the engine's own doctor. */
export const Route = createFileRoute("/_console/server_/docker/engine")({
  component: EnginePage,
});
