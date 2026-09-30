import type { Backup } from "../../api/queries/backups";
import { RelativeTime } from "../../components/page/RelativeTime";
import { Mono } from "../../components/ui/Mono";
import { useT } from "../../i18n";
import { RestoreDialog } from "./RestoreDialog";
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
  return (
    <RestoreDialog
      open={open}
      onOpenChange={onOpenChange}
      title={t("backups.restoreDialog.title", { domain: backup.domain })}
      description={t("backups.restoreDialog.description")}
      source={t.rich("backups.restoreDialog.source", { id: <Mono tone="default">{backup.backup_id}</Mono>, when: <RelativeTime value={backup.timestamp} /> })}
      domain={backup.domain}
      offerVerify
      envDescription={t("backups.restoreDialog.envFromArchive")}
      pending={restore.isPending}
      error={restore.error}
      onReset={() => restore.reset()}
      onRestore={({ targetDomain, restoreEnv, verify }) =>
        restore.mutate(
          {
            backupId: backup.backup_id,
            targetDomain: targetDomain === backup.domain ? undefined : targetDomain,
            restoreEnv,
            verify,
          },
          { onSuccess: () => onOpenChange(false) },
        )
      }
    />
  );
}
