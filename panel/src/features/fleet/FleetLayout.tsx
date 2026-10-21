import { Link, Outlet, useRouterState } from "@tanstack/react-router";
import { Cloud, Network } from "lucide-react";
import { useState } from "react";

import { LinkTabs } from "../../app/LinkTabs";
import { FLEET_TABS } from "../nav/fleet";
import { DetailPage } from "../../components/page/DetailPage";
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
import { FleetActionsProvider } from "./BulkActionDialog";

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
  const onApps = useRouterState({ select: (state) => state.location.pathname === "/fleet/apps" });
  const onServers = useRouterState({ select: (state) => state.location.pathname === "/fleet/servers" });

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

  return (
    <FleetActionsProvider>
      <DetailPage
        header={{
          title: t("fleet.page.title"),
          description: t("fleet.page.description"),
          ...(empty
            ? {}
            : {
                ...(onServers ? { secondaryActions: create } : {}),
                primaryAction: onApps ? (
                  <Link to="/apps/new" search={{ node: undefined }} className={buttonClassName("primary")}>
                    <Add aria-hidden="true" />
                    {t("fleet.apps.newApplication")}
                  </Link>
                ) : (
                  add
                ),
              }),
        }}
        {...(central.locked || hub !== undefined || central.role === "hub"
          ? {
              banner: (
                <div className="flex flex-col gap-3">
                  <CentralLockedNotice />
                  {hub !== undefined || central.role === "hub" ? (
                    <Notice title={t("servers.fleet.hub.title")}>{empty ? t("servers.fleet.hub.noServers") : t("servers.fleet.hub.description")}</Notice>
                  ) : null}
                </div>
              ),
            }
          : {})}
        {...(empty ? {} : { tabs: <LinkTabs label={t("fleet.page.tabsLabel")} tabs={FLEET_TABS} /> })}
      >
        {empty ? (
          <EmptyState
            variant="firstUse"
            icon={<Network />}
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
