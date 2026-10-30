import { useState } from "react";
import type { ReactNode } from "react";

import { RestoreDialog } from "../../components/page/RestoreDialog";
import { Checkbox } from "../../components/ui/Checkbox";
import { useT } from "../../i18n";

export interface RestoreRequest {
  /** Where to restore: the backup's own domain, or another the operator typed. */
  targetDomain: string;
  restoreEnv: boolean;
  /** Only asked for a local backup: its checksum is checked before anything is replaced. */
  verify: boolean;
}

export interface AppRestoreDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** What will be replaced, and what is lost. */
  replaceNote: ReactNode;
  /** The backup, named: its id and when it was made. */
  source: ReactNode;
  /** The application there is now to restore over, or null for a server that never had it. */
  domain: string | null;
  /** Offers the integrity check first (a local backup's checksum). */
  offerVerify: boolean;
  /** The .env option's help: from this archive, or from the one downloaded. */
  envDescription: string;
  /** Starts the restore; a rejection is shown in the dialog, verbatim, and it stays open. */
  onRestore: (request: RestoreRequest) => Promise<void>;
}

/**
 * Putting a backup back over an application, from a backup on this server or one on a destination
 * (which is downloaded first): the console's one restore confirmation (`RestoreDialog`) with this
 * thing's words and its two options. Over the application there is now it asks for its domain to be
 * typed; to another domain it restores a copy there and leaves this one alone. Replacing the `.env`
 * files destroys more, so it starts unchecked.
 */
export function AppRestoreDialog({ open, onOpenChange, title, replaceNote, source, domain, offerVerify, envDescription, onRestore }: AppRestoreDialogProps) {
  const t = useT();
  const [restoreEnv, setRestoreEnv] = useState(false);
  const [verify, setVerify] = useState(true);
  return (
    <RestoreDialog
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          setRestoreEnv(false);
          setVerify(true);
        }
      }}
      title={title}
      source={source}
      existing={domain}
      defaultTarget="replace"
      words={{
        into: t("backups.restoreDialog.into"),
        intoNew: t("backups.restoreDialog.intoNew"),
        intoNewHint: t("backups.restoreDialog.intoNewHint"),
        intoReplace: domain === null ? "" : t("backups.restoreDialog.intoReplace", { domain }),
        intoReplaceHint: t("backups.restoreDialog.intoReplaceHint"),
        replaceNote,
        newName: t("backups.restoreDialog.newName"),
        newNameHelp: t("backups.restoreDialog.newNameHelp"),
        nameInvalid: t("backups.restoreDialog.nameInvalid"),
        actionReplace: domain === null ? "" : t("backups.restoreDialog.actionReplace", { domain }),
        actionNew: t("backups.restoreDialog.actionNew"),
      }}
      isValidName={(name) => /^[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?(\.[A-Za-z0-9]([A-Za-z0-9-]*[A-Za-z0-9])?)+$/.test(name)}
      options={
        <>
          <Checkbox checked={restoreEnv} onCheckedChange={setRestoreEnv} label={t("backups.restoreDialog.restoreEnv")} description={envDescription} />
          {offerVerify ? (
            <Checkbox
              checked={verify}
              onCheckedChange={setVerify}
              label={t("backups.restoreDialog.verifyFirst.label")}
              description={t("backups.restoreDialog.verifyFirst.description")}
            />
          ) : null}
        </>
      }
      onRestore={({ name }) => onRestore({ targetDomain: name, restoreEnv, verify: offerVerify && verify })}
    />
  );
}
