import { Button } from "../../../components/ui/Button";
import { CopyTextButton } from "../../../components/ui/CopyTextButton";
import { Dialog } from "../../../components/ui/Dialog";
import { useT } from "../../../i18n";
import { restoreCommand, restoreServerCommand } from "./data";

function Command({ command, label }: { command: string; label: string }) {
  return (
    <div className="flex min-w-0 flex-col items-start gap-2">
      <code translate="no" className="w-full rounded-control border border-border-strong bg-bg-sunken px-3 py-2 text-12 break-all text-fg select-all">
        {command}
      </code>
      <CopyTextButton value={command} variant="secondary">
        {label}
      </CopyTextButton>
    </div>
  );
}

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
          <Command command={restoreCommand(name)} label={t("selfBackup.restore.copy")} />
        </div>
        <div className="flex min-w-0 flex-col gap-2">
          <p className="text-13 font-medium text-fg">{t("selfBackup.restore.second")}</p>
          <Command command={restoreServerCommand(name)} label={t("selfBackup.restore.copy")} />
        </div>
        <p className="max-w-measure text-13 text-fg-muted">{t("selfBackup.restore.note")}</p>
      </div>
    </Dialog>
  );
}
