import { createFileRoute } from "@tanstack/react-router";

import { useT } from "../../../i18n";
import { MachinePage } from "../../../features/server/MachinePage";
import { SecurityTab } from "../../../features/server/security/SecurityTab";

/** Security > ssh: a view of the Server's Security and SSH in 4.1 (`?view=ssh`), a section of Security now. */
export const Route = createFileRoute("/_console/security/ssh")({
  component: SshRoute,
});

function SshRoute() {
  const t = useT();
  return (
    <MachinePage title={t("server.security.views.ssh")}>
      <SecurityTab view="ssh" />
    </MachinePage>
  );
}
