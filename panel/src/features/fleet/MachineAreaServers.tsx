import { Link } from "@tanstack/react-router";

import { DetailPage } from "../../components/page/DetailPage";
import { buttonClassName } from "../../components/ui/Button";
import { Mono } from "../../components/ui/Mono";
import { RowList } from "../../components/ui/RowList";
import { StatusPill } from "../../components/ui/StatusPill";
import { OBJECT_ICONS } from "../../components/ui/objectIcons";
import { useT } from "../../i18n";
import { NODE_STATE, nodeStatus, useServerList } from "../../nodes/servers";
import { useCentral } from "../central/central";

/**
 * The machine's areas (Network, Security) from "All servers": the servers, to choose one. What these areas hold
 * belongs to one machine, so across the fleet there is nothing to merge: opening one on a server is choosing that
 * server in the selector, and the area opens on it. The summary of the area on each server is the area's own
 * lane's to add to each row.
 */
export function MachineAreaServers({ area }: { area: "network" | "security" }) {
  const t = useT();
  const servers = useServerList();
  const { role } = useCentral();
  const label = t(area === "network" ? "nav.network.label" : "nav.security.label");
  const path = area === "network" ? "/network" : "/security";
  // A hub deploys nothing of its own, so it has no machine's pages to open here; every other central is a server of the list.
  const central = role === "hub" ? [] : [{ name: null, label: servers.hostname ?? t("fleet.selector.thisServer") }];
  const rows = [...central, ...servers.nodes.map((node) => ({ name: node.name, label: node.name }))];
  return (
    <DetailPage header={{ title: label, description: t("fleet.machineArea.description", { area: label }) }}>
      <RowList aria-label={t("fleet.machineArea.server")}>
        {rows.map((row) => {
          const node = row.name === null ? undefined : servers.nodes.find((candidate) => candidate.name === row.name);
          const view = node === undefined ? undefined : NODE_STATE[nodeStatus(node)];
          return (
            <RowList.Item
              key={row.name ?? "central"}
              leading={<OBJECT_ICONS.server aria-hidden="true" className="size-icon-md text-fg-faint" />}
              actions={
                <Link to={path} search={{ node: row.name ?? undefined }} className={buttonClassName("secondary", "sm")}>
                  {t("fleet.machineArea.open", { area: label, server: row.label })}
                </Link>
              }
            >
              <span className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
                <Mono truncate className="font-medium">
                  {row.label}
                </Mono>
                {row.name === null ? <span className="text-12 text-fg-muted">{t("fleet.machineArea.central")}</span> : null}
                {view !== undefined ? <StatusPill state={view.state} label={t(view.label)} size="sm" /> : null}
              </span>
            </RowList.Item>
          );
        })}
      </RowList>
    </DetailPage>
  );
}
