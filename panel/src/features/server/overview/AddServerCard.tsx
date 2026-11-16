import { Link } from "@tanstack/react-router";

import { SurfaceSection } from "../../../components/page/SurfaceSection";
import { useCan } from "../../../components/page/permissions";
import { buttonClassName } from "../../../components/ui/Button";
import { OBJECT_ICONS } from "../../../components/ui/objectIcons";
import { useT } from "../../../i18n";
import { useHasFleet, useServerList } from "../../../nodes/servers";

/**
 * The way to a fleet from a server that has none (item 160): a section on the Server's own page that says what a fleet
 * is and opens "Add a server". Before it the only way in was the palette, so a person who had never heard of a fleet
 * could not know that this one server could be several. It is on the page only while the console holds no fleet (a
 * central with servers, or a hub, already has the buttons), only for whoever may manage one, and only once the list of
 * servers has been read, so it never shows for a moment on a fleet while the answer is on its way.
 */
export function AddServerCard() {
  const t = useT();
  const hasFleet = useHasFleet();
  const servers = useServerList();
  const mayManage = useCan("fleet.manage");
  if (hasFleet || !servers.loaded || !mayManage) return null;
  return (
    <SurfaceSection
      title={t("server.overview.fleet.title")}
      icon={OBJECT_ICONS.node}
      description={t("server.overview.fleet.description")}
      actions={
        <Link to="/fleet/servers" search={{ add: true }} className={buttonClassName("secondary", "md")}>
          {t("fleet.page.addServer")}
        </Link>
      }
    />
  );
}
