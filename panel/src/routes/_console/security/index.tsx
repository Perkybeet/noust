import { createFileRoute } from "@tanstack/react-router";

import { useT } from "../../../i18n";
import { MachinePage } from "../../../features/server/MachinePage";
import { SecurityTab } from "../../../features/server/security/SecurityTab";

/** Security > checks: a view of the Server's Security and SSH in 4.1 (`?view=checks`), a section of Security now. */
export const Route = createFileRoute("/_console/security/")({
  component: ChecksRoute,
});

function ChecksRoute() {
  const t = useT();
  return (
    <MachinePage title={t("server.security.views.checks")}>
      <SecurityTab view="checks" />
    </MachinePage>
  );
}
