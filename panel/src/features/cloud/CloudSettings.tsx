import { useQuery } from "@tanstack/react-query";
import { useState } from "react";

import { useDocumentTitle } from "../../app/documentTitle";
import { Sections } from "../../components/page/Section";
import { useT } from "../../i18n";
import { CentralLockedNotice } from "../central/CentralLockedNotice";
import { useCentral } from "../central/central";
import { AccountsSection } from "./AccountsSection";
import { CreateServerDialog } from "./CreateServerDialog";
import { cloudAccountsQuery } from "./data";
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
  const [adding, setAdding] = useState(false);
  const [creatingState, setCreatingState] = useState(false);
  const creating = creatingProp ?? creatingState;
  const setCreating = (next: boolean): void => {
    setCreatingState(next);
    onCreatingChange?.(next);
  };

  return (
    <Sections>
      {central.locked ? <CentralLockedNotice /> : null}
      <MachinesSection
        canCreate={(accounts.data?.length ?? 0) > 0}
        onCreate={() => {
          setCreating(true);
        }}
      />
      <AccountsSection adding={adding} onAddingChange={setAdding} />
      <ScriptsSection />
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

