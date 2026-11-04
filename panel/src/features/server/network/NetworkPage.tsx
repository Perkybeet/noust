/**
 * Network and ports (4.1, task 11: open a port, or see who uses one). What is open to the network
 * and who holds each port is the listening ports; the firewall that decides who may reach them and the
 * interfaces are the others, each a section of Network (4.2: the column lists them, and the route says
 * which view this is). The question "who uses port 5432?" is the search box of the first one. Publishing
 * an application on a port of its own is the section of Network that comes first, and a line here leads there.
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
import type { NetworkSearch, NetworkView } from "./data";

export interface NetworkPageProps {
  search: NetworkSearch;
  onSearchChange: (search: NetworkSearch, options?: { replace?: boolean }) => void;
  /** The view this page is: the route's, since each is a section of Network. Without it the address's `?view=` says. */
  view?: NetworkView;
}

function Network({ search, onSearchChange, view: fixed }: NetworkPageProps) {
  const t = useT();
  const view: NetworkView = fixed ?? search.view ?? "ports";
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
      <p className="max-w-measure text-13 text-pretty text-fg-muted">
        {t.rich("serverPorts.publishedElsewhere", {
          link: (
            <TextLink key="link" to="/network" size="inline">
              {t("domains.sections.ports.tab")}
            </TextLink>
          ),
        })}
      </p>
      {view === "ports" && firewall.data !== undefined && !active && firewall.data.firewall.backend !== "none" ? (
        <Notice tone="warning" title={t("serverPorts.firewallOff.title")}>
          <p>
            {t("serverPorts.firewallOff.body")}{" "}
            <TextLink to="/network/firewall" size="inline">
              {t("serverPorts.firewallOff.link")}
            </TextLink>
          </p>
        </Notice>
      ) : null}
      {fixed === undefined ? (
        <SegmentedControl<NetworkView>
          label={t("serverPorts.viewsLabel")}
          value={view}
          onValueChange={setView}
          options={[
            { value: "ports", label: t("serverPorts.views.ports") },
            { value: "firewall", label: t("serverPorts.views.firewall") },
            { value: "interfaces", label: t("serverPorts.views.interfaces") },
          ]}
          className="self-start"
        />
      ) : null}
      {view === "firewall" ? (
        <FirewallView />
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
