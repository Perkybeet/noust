import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { useDocumentTitle } from "../../app/documentTitle";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { Sections } from "../../components/page/Section";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { CentralLockedNotice } from "../central/CentralLockedNotice";
import { useCentral } from "../central/central";
import { AccountsSection } from "./AccountsSection";
import { CreateServerDialog } from "./CreateServerDialog";
import { cloudAccountsQuery, cloudMachinesQuery } from "./data";
import { MachinesSection } from "./MachinesSection";
import { ScriptsSection } from "./ScriptsSection";

export interface CloudSettingsProps {
  /** The create-a-server wizard is open (the URL says so, so Settings > Servers can link straight to it). */
  creating?: boolean;
  onCreatingChange?: (creating: boolean) => void;
}

/**
 * Settings > Cloud: servers made at Hetzner, DigitalOcean or Vultr. The accounts a central holds
 * tokens for, the machines it created (and the one that did not make it, with the reason), and the
 * library of boot scripts a new server can run. Creating a server is a wizard, here or from
 * Settings > Servers.
 */
export function CloudSettings({ creating: creatingProp, onCreatingChange }: CloudSettingsProps) {
  const t = useT();
  useDocumentTitle(t("cloud.page.documentTitle"), 1);
  const central = useCentral();
  const accounts = useQuery(cloudAccountsQuery());
  const machines = useQuery(cloudMachinesQuery());
  const [adding, setAdding] = useState(false);
  const [creatingState, setCreatingState] = useState(false);
  const creating = creatingProp ?? creatingState;
  const setCreating = (next: boolean): void => {
    setCreatingState(next);
    onCreatingChange?.(next);
  };

  // The servers and the accounts decide what their sections hold (a table, or an invitation to
  // add the first one, worded by whether an account exists), and either can answer first. Nothing
  // is drawn until both have, or each section would grow as its answer came and push down the
  // ones below it. The scripts are last, so they can arrive on their own.
  const reading = machines.isPending || accounts.isPending;

  return (
    <Sections>
      {central.locked ? <CentralLockedNotice /> : null}
      {reading ? (
        <LoadingRegion label={t("cloud.page.loadingLabel")}>
          <Skeleton className="h-64 w-full rounded-card" />
        </LoadingRegion>
      ) : (
        <>
          <MachinesSection
            canCreate={(accounts.data?.length ?? 0) > 0}
            onCreate={() => {
              setCreating(true);
            }}
          />
          <AccountsSection adding={adding} onAddingChange={setAdding} />
          <ScriptsSection />
        </>
      )}
      <CreateServerDialog
        open={creating}
        onClose={() => {
          setCreating(false);
        }}
        onAddAccount={() => {
          setCreating(false);
          setAdding(true);
        }}
      />
    </Sections>
  );
}

