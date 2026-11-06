import { createFileRoute } from "@tanstack/react-router";

import { SshPage } from "../../../features/server/security/SecurityPages";

/** Security > ssh: a view of the Server's Security and SSH in 4.1 (`?view=ssh`), a section of Security now. */
export const Route = createFileRoute("/_console/security/ssh")({
  component: SshPage,
});
