import { useQuery } from "@tanstack/react-query";
import { Plus } from "lucide-react";
import { useState } from "react";

import { useDocumentTitle } from "../../app/documentTitle";
import { LoadingRegion } from "../../components/page/LoadingRegion";
import { GuardedButton, ReadOnlyLine } from "../../components/page/permissions";
import { Sections } from "../../components/page/Section";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { CentralLockedNotice } from "../central/CentralLockedNotice";
import { useCentral } from "../central/central";
import { SettingsPrimaryAction } from "../settings/SettingsPrimaryAction";
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
 * Settings > Cloud: servers made at Hetzner, DigitalOcean or Vultr. Three slabs, each saying what it is before a
 * word of it is read: the accounts a central holds tokens for, the machines it created (and the one that did not
 * make it, with the provider's own words), and the library of boot scripts a new server can run. Creating a server is
 * the page's one primary action, in its header, and a wizard; without an account it waits, and says what for.
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
  // ones below it. The scripts are last, so they can arrive on their own. An answer that failed
  // is an answer (the section says what failed), and "has answered once" is what is asked, not
  // "is not pending": a query that failed starts over as pending when a section mounts and asks
  // again, which would take the sections away again, and so on.
  const reading = !machines.isFetched || !accounts.isFetched;
  const canCreate = (accounts.data?.length ?? 0) > 0;

  return (
    <Sections surfaces>
      {central.locked ? <CentralLockedNotice /> : null}
      {reading ? null : (
        <SettingsPrimaryAction>
          <GuardedButton
            permission="fleet.manage"
            variant="primary"
            icon={<Plus aria-hidden="true" />}
            onClick={() => {
              setCreating(true);
            }}
            {...(canCreate ? {} : { unavailable: t("cloud.machines.empty.needsAccount") })}
          >
            {t("cloud.machines.create")}
          </GuardedButton>
        </SettingsPrimaryAction>
      )}
      <ReadOnlyLine permission="fleet.manage" />
      {reading ? (
        <LoadingRegion label={t("cloud.page.loadingLabel")}>
          <Skeleton className="h-64 w-full rounded-card" />
        </LoadingRegion>
      ) : (
        <>
          <AccountsSection adding={adding} onAddingChange={setAdding} />
          <MachinesSection canCreate={canCreate} />
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
