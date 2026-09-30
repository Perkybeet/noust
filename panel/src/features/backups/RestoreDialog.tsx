import { useState } from "react";
import type { ReactNode } from "react";

import { Checkbox } from "../../components/ui/Checkbox";
import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";

export interface RestoreRequest {
  /** Where to restore: the backup's own domain, or another the operator typed. */
  targetDomain: string;
  restoreEnv: boolean;
  /** Only asked for a local backup: its checksum is checked before anything is replaced. */
  verify: boolean;
}

export interface RestoreDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  /** What will be replaced, and what is lost. */
  description: ReactNode;
  /** The backup, named: its id and when it was made. */
  source: ReactNode;
  /** The domain the backup belongs to, when known: the target it proposes. */
  domain: string;
  /** Offers the integrity check first (a local backup's checksum). */
  offerVerify: boolean;
  /** The .env option's help: from this archive, or from the one downloaded. */
  envDescription: string;
  /** Starts the restore; a rejection is shown in the dialog, verbatim, and it stays open. */
  onRestore: (request: RestoreRequest) => Promise<void>;
}

/**
 * Putting a backup back over an application: data written since is lost for good, so it is
 * the system's confirmation with friction "type" (docs/DESIGN.md 6.2), its options between the
 * question and the name. The name to type is the target itself, which doubles as where to
 * restore: a backup may be replayed onto another domain than the one it came from. One dialog
 * for a local backup and for one on a destination, which is downloaded first. Replacing the
 * `.env` files destroys more, so it starts unchecked and the description says what it does.
 */
export function RestoreDialog({ open, onOpenChange, title, description, source, domain, offerVerify, envDescription, onRestore }: RestoreDialogProps) {
  const t = useT();
  const [edited, setEdited] = useState<string | null>(null);
  const target = edited ?? domain;
  const [restoreEnv, setRestoreEnv] = useState(false);
  const [verify, setVerify] = useState(true);
  const named = target.trim() !== "";

  return (
    <ConfirmDialog
      friction="type"
      open={open}
      onOpenChange={(next) => {
        onOpenChange(next);
        if (!next) {
          setEdited(null);
          setRestoreEnv(false);
          setVerify(true);
        }
      }}
      title={title}
      description={
        <>
          {description} {restoreEnv ? t("backups.restoreDialog.envReplaced") : t("backups.restoreDialog.envKept")}
        </>
      }
      actionLabel={t("backups.restoreDialog.submit")}
      confirmText={named ? target : ""}
      ready={named}
      onConfirm={() => onRestore({ targetDomain: target, restoreEnv, verify: offerVerify && verify })}
    >
      <p className="text-13 text-fg-muted">{source}</p>
      <Field label={t("backups.restoreDialog.restoreInto")} description={t("backups.restoreDialog.restoreIntoDescription")}>
        <Input mono value={target} onValueChange={setEdited} autoComplete="off" autoCapitalize="off" spellCheck={false} />
      </Field>
      <Checkbox checked={restoreEnv} onCheckedChange={setRestoreEnv} label={t("backups.restoreDialog.restoreEnv")} description={envDescription} />
      {offerVerify ? (
        <Checkbox
          checked={verify}
          onCheckedChange={setVerify}
          label={t("backups.restoreDialog.verifyFirst.label")}
          description={t("backups.restoreDialog.verifyFirst.description")}
        />
      ) : null}
    </ConfirmDialog>
  );
}
