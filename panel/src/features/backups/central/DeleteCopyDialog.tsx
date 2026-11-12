import type { CentralCopy } from "../../../api/queries/centralBackups";
import { RelativeTime } from "../../../components/page/RelativeTime";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Mono } from "../../../components/ui/Mono";
import { useT } from "../../../i18n";
import { useCentralBackupActions } from "./useCentralBackupActions";

export interface DeleteCopyDialogProps {
  copy: CentralCopy;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Letting a copy go from the central. One question (friction "simple", docs/DESIGN.md 6.2): the server is not asked for it
 * again and whatever the server still has of its own stays there, so the one place of the copy that goes is this one.
 */
export function DeleteCopyDialog({ copy, open, onOpenChange }: DeleteCopyDialogProps) {
  const t = useT();
  const { remove } = useCentralBackupActions();
  return (
    <ConfirmDialog
      friction="simple"
      destructive
      open={open}
      onOpenChange={onOpenChange}
      title={t("centralBackups.delete.title")}
      description={t.rich("centralBackups.delete.description", {
        owner: <Mono tone="default">{copy.owner}</Mono>,
        when: <RelativeTime value={copy.created_at} />,
        server: <Mono tone="default">{copy.node}</Mono>,
      })}
      actionLabel={t("centralBackups.delete.action")}
      onConfirm={async () => {
        // design-exception: write-without-permission the dialog is opened only from a row drawn for backups.manage, and the server decides again
        await remove.mutateAsync({ node: copy.node, kind: copy.kind, item: copy.id, owner: copy.owner });
      }}
    />
  );
}
