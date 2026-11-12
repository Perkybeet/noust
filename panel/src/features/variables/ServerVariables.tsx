import { useDocumentTitle } from "../../app/documentTitle";
import { useT } from "../../i18n";
import { FleetBlock } from "../settings/fleet/FleetBlock";
import { FleetStrip } from "../settings/fleet/FollowLine";
import { SharedVariables } from "./SharedVariables";
import { useCan } from "../../components/page/permissions";

/**
 * The server's own shared variables, a section of Settings: defined once here, every
 * application on the server refers to one with `{{server.NAME}}` (a region, a mail host, an
 * observability endpoint). The add action is the page's one primary, in its header.
 */
export function ServerVariables() {
  const t = useT();
  useDocumentTitle(t("variables.nav.settings.documentTitle"), 1);
  const canManage = useCan("settings.manage");
  return (
    <div className="flex flex-col gap-6">
      <FleetStrip />
      <SharedVariables scope={{ kind: "server" }} canManage={canManage} primary />
      {/* The variables every server of the fleet has, with the servers that follow each one. */}
      <FleetBlock resource="variable" />
    </div>
  );
}
