import { createFileRoute } from "@tanstack/react-router";

import { BlocksPage } from "../../../features/server/security/SecurityPages";

/** Security > blocks: a view of the Server's Security and SSH in 4.1 (`?view=bans`, "Brute force"), a section of Security now. */
export const Route = createFileRoute("/_console/security/blocks")({
  component: BlocksPage,
});
