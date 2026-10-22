import { Button } from "../../../components/ui/Button";
import { CopyValue } from "../../../components/ui/CopyValue";
import { Dialog } from "../../../components/ui/Dialog";
import { useT } from "../../../i18n";
import { restoreCommand, restoreServerCommand } from "./data";

export interface RestoreInfoDialogProps {
  name: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * How to restore a bundle: the commands, never a button. A restore replaces the store the
 * console runs on and stops the console, the monitor and the timers while it does, so only a
 * terminal on the server survives it; the console says so and gives the exact command.
 */
export function RestoreInfoDialog({ name, open, onOpenChange }: RestoreInfoDialogProps) {
  const t = useT();
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={t("selfBackup.restore.title", { name })}
      description={t("selfBackup.restore.description")}
      footer={
        <Button variant="primary" onClick={() => onOpenChange(false)}>
          {t("common.words.close")}
        </Button>
      }
    >
      <div className="flex min-w-0 flex-col gap-5">
        <div className="flex min-w-0 flex-col gap-2">
          <p className="text-13 font-medium text-fg">{t("selfBackup.restore.first")}</p>
          <CopyValue value={restoreCommand(name)} label={t("selfBackup.restore.copy")} />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <p className="text-13 font-medium text-fg">{t("selfBackup.restore.second")}</p>
          <CopyValue value={restoreServerCommand(name)} label={t("selfBackup.restore.copy")} />
        </div>
        <p className="max-w-measure text-13 text-fg-muted">{t("selfBackup.restore.note")}</p>
      </div>
    </Dialog>
  );
}
