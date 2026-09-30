import { useState } from "react";

import { CommandHint } from "../../../components/page/CommandHint";
import { DangerAction, DangerZone } from "../../../components/page/DangerZone";
import { Button } from "../../../components/ui/Button";
import { ICONS } from "../../../components/ui/icons";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import { DeleteAppDialog } from "../DeleteAppDialog";
import { useSudoFirst } from "./formParts";
import { useSettingsApp, useSubsectionTitle } from "./settingsApp";

/**
 * Delete, the last subsection and set apart: what deleting always takes (the service and the
 * site), what it only takes when asked (the files, the certificate: both unticked in the
 * dialog) and what it never takes (backups). The dialog is the one the header's menu opens,
 * after "Confirm it's you", with the domain to type.
 */
export function DeleteSettings() {
  const t = useT();
  const app = useSettingsApp();
  const domain = app.domain;
  useSubsectionTitle("appSettings.nav.delete", domain);
  const sudoFirst = useSudoFirst();
  const [open, setOpen] = useState(false);
  const Delete = ICONS.delete;

  const start = (): void => {
    // Sudo mode first, so "Confirm it's you" never opens on top of the typed confirmation.
    sudoFirst(() => setOpen(true), t("appSettings.danger.startFailed", { domain }));
  };

  return (
    <>
      <DangerZone title={t("appSettings.danger.title")} description={t("appSettings.danger.description")}>
      <DangerAction
        title={t("appSettings.danger.actionTitle")}
        description={
          app.path
            ? t.rich("appSettings.danger.actionDescriptionWithPath", { path: <Mono tone="muted">{app.path}</Mono> })
            : t("appSettings.danger.actionDescription")
        }
        action={
          <Button variant="danger" icon={<Delete aria-hidden="true" />} onClick={start}>
            {t("appSettings.danger.deleteButton")}
          </Button>
        }
      />
      </DangerZone>
      <CommandHint command={`noust delete ${domain} --keep-files`} label={t("appSettings.fromTerminal")} />
      <DeleteAppDialog app={app} open={open} onOpenChange={setOpen} />
    </>
  );
}
