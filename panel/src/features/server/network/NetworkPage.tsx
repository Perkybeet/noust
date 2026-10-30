/**
 * Network and ports (4.1, task 11: open a port, or see who uses one). What is open to the network
 * and who holds each port is the first view; the firewall that decides who may reach them, the
 * ports published in front of a service that is not a website, and the interfaces are the others,
 * each a URL (`?view=`). The question "who uses port 5432?" is the search box of the first one.
 */

import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { SegmentedControl } from "../../../components/page/SegmentedControl";
import { Button } from "../../../components/ui/Button";
import { Notice } from "../../../components/ui/Notice";
import { TextLink } from "../../../components/ui/TextLink";
import { useT } from "../../../i18n";
import { NodeCapabilityGate } from "../../../nodes/capability";
import { SERVER_CAPABILITY, firewallQuery } from "../queries";
import { AddRuleDialog, FirewallView } from "../security/FirewallView";
import { TabToolbar } from "../TabToolbar";
import { InterfacesView } from "./InterfacesView";
import { PortsView } from "./PortsView";
import { PublishedPorts } from "./PublishedPorts";
import type { NetworkSearch, NetworkView } from "./data";

export interface NetworkPageProps {
  search: NetworkSearch;
  onSearchChange: (search: NetworkSearch, options?: { replace?: boolean }) => void;
}

function Network({ search, onSearchChange }: NetworkPageProps) {
  const t = useT();
  const view: NetworkView = search.view ?? "ports";
  const firewall = useQuery(firewallQuery());
  // A port is opened through the firewall; with none filtering, every port that listens is open already.
  const active = firewall.data?.firewall.active === true;
  const [opening, setOpening] = useState<string | null>(null);
  const setView = (next: NetworkView): void => onSearchChange(next === "ports" ? {} : { view: next }, { replace: true });
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <TabToolbar
        summary={t("serverPorts.intro")}
        {...(view === "ports" && active
          ? {
              actions: (
                <Button variant="primary" icon={<Plus aria-hidden="true" />} onClick={() => setOpening("")}>
                  {t("serverPorts.open")}
                </Button>
              ),
            }
          : {})}
      />
      {view === "ports" && firewall.data !== undefined && !active && firewall.data.firewall.backend !== "none" ? (
        <Notice tone="warning" title={t("serverPorts.firewallOff.title")}>
          <p>
            {t("serverPorts.firewallOff.body")}{" "}
            <TextLink to="/server/network" search={{ view: "firewall" }} size="inline">
              {t("serverPorts.firewallOff.link")}
            </TextLink>
          </p>
        </Notice>
      ) : null}
      <SegmentedControl<NetworkView>
        label={t("serverPorts.viewsLabel")}
        value={view}
        onValueChange={setView}
        options={[
          { value: "ports", label: t("serverPorts.views.ports") },
          { value: "firewall", label: t("serverPorts.views.firewall") },
          { value: "published", label: t("serverPorts.views.published") },
          { value: "interfaces", label: t("serverPorts.views.interfaces") },
        ]}
        className="self-start"
      />
      {view === "firewall" ? (
        <FirewallView />
      ) : view === "published" ? (
        <PublishedPorts />
      ) : view === "interfaces" ? (
        <InterfacesView />
      ) : (
        <PortsView query={search.q ?? ""} onQueryChange={(q) => onSearchChange(q.trim() === "" ? {} : { q }, { replace: true })} {...(active ? { onOpen: (port: number) => setOpening(String(port)) } : {})} />
      )}
      {opening !== null ? <AddRuleDialog key={opening} port={opening} onClose={() => setOpening(null)} /> : null}
    </div>
  );
}

/** Network and ports. */
export function NetworkPage(props: NetworkPageProps) {
  return (
    <NodeCapabilityGate capability={SERVER_CAPABILITY}>
      <Network {...props} />
    </NodeCapabilityGate>
  );
}
