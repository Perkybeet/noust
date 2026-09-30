import { Outlet } from "@tanstack/react-router";
import { Network, Play } from "lucide-react";
import { useState } from "react";

import { LinkTabs } from "../../app/LinkTabs";
import { FLEET_TABS } from "../../app/nav";
import { DetailPage } from "../../components/page/DetailPage";
import { Button } from "../../components/ui/Button";
import { EmptyState } from "../../components/ui/EmptyState";
import { ICONS } from "../../components/ui/icons";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { useServerList } from "../../nodes/servers";
import type { HubArea } from "../central/central";
import { useCentral } from "../central/central";
import { CentralLockedNotice } from "../central/CentralLockedNotice";
import { AddServerDialog } from "../settings/servers/AddServerDialog";
import { FleetActionsProvider, useFleetActions } from "./BulkActionDialog";

export interface FleetLayoutProps {
  /** Set when a hub was asked for one of the pages it does not have. */
  hub?: HubArea | "overview" | undefined;
}

function RunAction() {
  const t = useT();
  const actions = useFleetActions();
  return (
    <Button
      icon={<Play aria-hidden="true" />}
      onClick={() => {
        actions.open();
      }}
    >
      {t("fleet.page.runAction")}
    </Button>
  );
}

/**
 * The fleet, every server at once (the "All servers" context): one header - Add a server,
 * Run an action - and a view per URL: the summary, the servers, and every server's
 * applications, certificates, backups, updates and activity side by side. Each view is the
 * central's one aggregated answer; a server that is down is a row of it, never a blank page.
 */
export function FleetLayout({ hub }: FleetLayoutProps) {
  const t = useT();
  const central = useCentral();
  const servers = useServerList();
  const [adding, setAdding] = useState(false);
  const Add = ICONS.add;
  const empty = servers.loaded && servers.nodes.length === 0;

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

  return (
    <FleetActionsProvider>
      <DetailPage
        header={{
          title: t("fleet.page.title"),
          description: t("fleet.page.description"),
          ...(empty ? {} : { secondaryActions: <RunAction />, primaryAction: add }),
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
            action={add}
            command="noust node key web-2"
          />
        ) : (
          <Outlet />
        )}
      </DetailPage>
      <AddServerDialog
        open={adding}
        onClose={() => {
          setAdding(false);
        }}
      />
    </FleetActionsProvider>
  );
}
