import type { Backup } from "../../api/queries/backups";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import { RestoreDialog } from "./RestoreDialog";
import { schemaChangeRefusal } from "../app/schemaChange";
import { useSchemaChangeConfirmation } from "../app/SchemaChangeDialog";
import { useBackupActions } from "./useBackupActions";

export interface RestoreBackupDialogProps {
  backup: Backup;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/** Restores an application from one of its backups on this server. */
export function RestoreBackupDialog({ backup, open, onOpenChange }: RestoreBackupDialogProps) {
  const t = useT();
  const { restore } = useBackupActions();
  const schema = useSchemaChangeConfirmation(backup.domain);
  return (
    <>
    <RestoreDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("backups.restoreDialog.title", { domain: backup.domain })}
      description={t("backups.restoreDialog.description")}
      source={t.rich("backups.restoreDialog.source", { id: <Mono tone="default">{backup.backup_id}</Mono>, when: <RelativeTime value={backup.timestamp} /> })}
      domain={backup.domain}
      offerVerify
      envDescription={t("backups.restoreDialog.envFromArchive")}
      onRestore={async ({ targetDomain, restoreEnv, verify }) => {
        const input = { backupId: backup.backup_id, targetDomain: targetDomain === backup.domain ? undefined : targetDomain, restoreEnv, verify };
        try {
          await restore.mutateAsync(input);
        } catch (error) {
          // Putting files back past a schema change asks first, naming the deployments.
          if (!schema.intercept(schemaChangeRefusal(error), () => restore.mutateAsync({ ...input, schemaChangedOk: true }))) throw error;
        }
      }}
    />
    {schema.dialog}
    </>
  );
}
