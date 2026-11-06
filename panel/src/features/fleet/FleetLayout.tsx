import { Link, Outlet } from "@tanstack/react-router";
import { Cloud, RotateCw } from "lucide-react";
import { useState } from "react";

import { useRenderedPathname } from "../../app/renderedPath";
import { DetailPage } from "../../components/page/DetailPage";
import { Can } from "../../components/page/permissions";
import { Button, buttonClassName } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { useServerList } from "../../nodes/servers";
import type { HubArea } from "../central/central";
import { useCentral } from "../central/central";
import { CentralLockedNotice } from "../central/CentralLockedNotice";
import { AddServer } from "./enroll/AddServer";
import { FleetActionsProvider, useFleetActions } from "./BulkActionDialog";
import { noustOf, noustUpdatable, originOf } from "./data";
import { useFleetView } from "./parts";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";

/**
 * The faces of an area that is a page of its own (Activity, Backups, Alerts, and Network and Security, the machine's areas): the same page as the area's, with
 * its own header, a title of its own and the server filter, so the fleet's header is not drawn over it.
 */
const OWN_HEADER: ReadonlySet<string> = new Set(["/fleet/activity", "/fleet/backups", "/fleet/alerts", "/fleet/network", "/fleet/security"]);

/**
 * "Update Noust on every server": the one press that does what each row's own does, for the servers whose Noust has a
 * newer one waiting. It is in the header of the servers' views because that is where the version is read, and it is
 * not drawn when there is nothing to update. It is inside the actions' provider, which the layout draws.
 */
function UpdateAll() {
  const t = useT();
  const view = useFleetView("summary");
  const actions = useFleetActions();
  // A bulk action never runs on the central itself: it updates last, from its own page, so it can watch every server come back.
  const nodes = (view.data?.items ?? []).filter((row) => !originOf(row).local && noustUpdatable(noustOf(row).state)).map((row) => originOf(row).node);
  if (nodes.length === 0) return null;
  return (
    <Can permission="fleet.manage">
      <Button
        icon={<RotateCw aria-hidden="true" />}
        onClick={() => {
          actions.open({ action: "noust_update", nodes });
        }}
      >
        {t("fleet.page.updateAll")}
      </Button>
    </Can>
  );
}

export interface FleetLayoutProps {
  /** Set when a hub was asked for one of the pages it does not have. */
  hub?: HubArea | "overview" | undefined;
  /** The "Add a server" flow is open: the URL says so, so the palette and old links can open it. */
  adding?: boolean | undefined;
  onAddingChange?: ((adding: boolean) => void) | undefined;
}

/**
 * The fleet, every server at once (the "All servers" context): one header with one primary -
 * New application on the Applications view, Add a server on the others; what can be run on
 * several servers lives with the view it acts on - and a view per URL: the summary, the servers, and every server's
 * applications, certificates, backups, updates and activity side by side. Each view is the
 * central's one aggregated answer; a server that is down is a row of it, never a blank page.
 */
export function FleetLayout({ hub, adding: addingProp, onAddingChange }: FleetLayoutProps) {
  const t = useT();
  const central = useCentral();
  const servers = useServerList();
  const [addingState, setAddingState] = useState(false);
  const adding = addingProp ?? addingState;
  const setAdding = (next: boolean): void => {
    setAddingState(next);
    onAddingChange?.(next);
  };
  const Add = ICONS.add;
  const empty = servers.loaded && servers.nodes.length === 0;
  // What the layout draws follows the page that is on screen, not the one being loaded: the router's location moves the
  // moment a link is pressed, and a header drawn from it appeared above the previous page's own (two titles) until the next
  // page had arrived.
  const pathname = useRenderedPathname();
  // Home and the applications across the fleet have their own templates, header and all.
  const ownsPage = ["/fleet", "/fleet/", "/fleet/apps"].includes(pathname);
  const onAppsViews = pathname === "/fleet/apps" || pathname === "/fleet/placements";
  const onServers = pathname === "/fleet/servers";
  // The list of servers, whole or only the ones that are waiting for something: both are where they are updated.
  const onServersList = onServers || pathname === "/fleet/updates";
  const onServersViews = pathname === "/fleet/servers" || pathname === "/fleet/updates" || pathname === "/fleet/software";
  const ownHeader = OWN_HEADER.has(pathname);

  const add = (
    <Button
      variant="primary"
      icon={<Add aria-hidden="true" />}
      onClick={() => {
        setAdding(true);
      }}
    >
      {t("fleet.page.addServer")}
    </Button>
  );

  // A server that does not exist yet: made at a provider, joined by this central itself.
  const create = (
    <Link to="/settings/cloud" search={{ create: true }} className={buttonClassName("secondary", "md")}>
      <Cloud aria-hidden="true" className="size-icon-sm" />
      {t("fleet.page.createAtProvider")}
    </Link>
  );

  const notices =
    central.locked || hub !== undefined || central.role === "hub" ? (
      <div className="flex flex-col gap-3">
        <CentralLockedNotice />
        {hub !== undefined || central.role === "hub" ? (
          <Notice title={t("servers.fleet.hub.title")}>{empty ? t("servers.fleet.hub.noServers") : t("servers.fleet.hub.description")}</Notice>
        ) : null}
      </div>
    ) : null;

  // A page with a header of its own (Home, the applications, an area's face): this layout keeps only what is the
  // fleet's to say above it.
  if ((ownsPage || ownHeader) && !empty) {
    return (
      <FleetActionsProvider>
        <div className="flex min-w-0 flex-col gap-6">
          {notices}
          <Outlet />
        </div>
        <AddServer
          open={adding}
          onClose={() => {
            setAdding(false);
          }}
        />
      </FleetActionsProvider>
    );
  }

  return (
    <FleetActionsProvider>
      <DetailPage
        header={{
          // The applications' second view is the same area as their list: the same title.
          title: onAppsViews ? t("apps.page.title") : onServersViews ? t("fleet.page.serversTitle") : t("fleet.page.title"),
          ...(onAppsViews ? {} : { description: onServersViews ? t("fleet.page.serversDescription") : t("fleet.page.description") }),
          ...(empty
            ? {}
            : {
                ...(onServersList ? { secondaryActions: <>{onServers ? create : null}<UpdateAll /></> } : {}),
                primaryAction: add,
              }),
        }}
        {...(notices !== null ? { banner: notices } : {})}
      >
        {empty ? (
          <EmptyState
            variant="firstUse"
            icon={<OBJECT_ICONS.node />}
            title={t("fleet.page.empty.title")}
            description={t("fleet.page.empty.description")}
            action={
              <>
                {add}
                {create}
              </>
            }
            command="noust node key web-2"
          />
        ) : (
          <Outlet />
        )}
      </DetailPage>
      <AddServer
        open={adding}
        onClose={() => {
          setAdding(false);
        }}
      />
    </FleetActionsProvider>
  );
}
