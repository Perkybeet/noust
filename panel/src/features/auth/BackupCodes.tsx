import { Download } from "lucide-react";
import type { RefObject } from "react";

import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { CopyTextButton } from "../../components/ui/CopyTextButton";
import { Dialog } from "../../components/ui/Dialog";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { downloadText } from "../../lib/clipboard";
import { backupCodesFile } from "../settings/security";

export interface BackupCodesPanelProps {
  codes: readonly string[];
  hostname: string;
  saved: boolean;
  /** The operator tried to go on without ticking the box: say why they cannot yet. */
  nudge: boolean;
  savedRef?: RefObject<HTMLDivElement | null>;
  onSavedChange: (saved: boolean) => void;
}

/**
 * A set of backup codes, the only time it is shown: copy, download, and a box to tick before
 * going on, since leaving loses the codes for good.
 */
export function BackupCodesPanel({ codes, hostname, saved, nudge, savedRef, onSavedChange }: BackupCodesPanelProps) {
  const t = useT();
  return (
    <div className="flex flex-col gap-4">
      <ul aria-label={t("settings.security.twoFactor.backupCodes.listLabel")} className="grid grid-cols-2 gap-x-6 gap-y-2 rounded-control border border-border bg-bg-sunken px-4 py-3">
        {codes.map((backup) => (
          <li key={backup} className="text-14 tracking-wide select-all">
            <Mono tone="default">{backup}</Mono>
          </li>
        ))}
      </ul>
      <div className="flex flex-wrap items-center gap-2">
        <CopyTextButton value={codes.join("\n")} size="sm">
          {t("settings.security.twoFactor.backupCodes.copyCodes")}
        </CopyTextButton>
        <Button
          size="sm"
          icon={<Download aria-hidden="true" />}
          onClick={() => {
            downloadText(`noust-backup-codes-${hostname}.txt`, backupCodesFile(codes, hostname, t.locale));
          }}
        >
          {t("settings.security.twoFactor.backupCodes.downloadAsText")}
        </Button>
      </div>
      <div ref={savedRef} className="flex flex-col gap-2">
        <Checkbox label={t("settings.security.twoFactor.backupCodes.savedCheckbox")} checked={saved} onCheckedChange={onSavedChange} />
        {nudge ? (
          <Notice tone="warning" live>
            {t("settings.security.twoFactor.backupCodes.nudge")}
          </Notice>
        ) : null}
      </div>
    </div>
  );
}

export interface BackupCodesDialogProps extends BackupCodesPanelProps {
  open: boolean;
  description: string;
  onOpenChange: (open: boolean) => void;
}

/** The codes in a dialog that does not let go until the box is ticked. */
export function BackupCodesDialog({ open, description, onOpenChange, ...panel }: BackupCodesDialogProps) {
  const t = useT();
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      title={t("settings.security.twoFactor.backupCodes.title")}
      description={description}
      footer={
        <Button
          variant="primary"
          disabled={!panel.saved}
          onClick={() => {
            onOpenChange(false);
          }}
        >
          {t("settings.shared.done")}
        </Button>
      }
    >
      <BackupCodesPanel {...panel} />
    </Dialog>
  );
}
