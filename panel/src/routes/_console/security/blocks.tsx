import { createFileRoute } from "@tanstack/react-router";

import { useT } from "../../../i18n";
import { MachinePage } from "../../../features/server/MachinePage";
import { SecurityTab } from "../../../features/server/security/SecurityTab";

/** Security > bans: a view of the Server's Security and SSH in 4.1 (`?view=bans`), a section of Security now. */
export const Route = createFileRoute("/_console/security/blocks")({
  component: BlocksRoute,
});

function BlocksRoute() {
  const t = useT();
  return (
    <MachinePage title={t("server.security.views.bans")}>
      <SecurityTab view="bans" />
    </MachinePage>
  );
}
