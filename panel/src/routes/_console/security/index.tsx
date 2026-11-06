import { createFileRoute } from "@tanstack/react-router";

import { ChecksPage } from "../../../features/server/security/SecurityPages";

/** Security > checks: a view of the Server's Security and SSH in 4.1 (`?view=checks`), a section of Security now. */
export const Route = createFileRoute("/_console/security/")({
  component: ChecksPage,
});
