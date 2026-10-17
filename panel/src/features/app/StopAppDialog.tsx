import { ConfirmDialog } from "../../components/ui/ConfirmDialog";
import { useT } from "../../i18n";
import { useNode } from "../../nodes/useNode";

export interface StopAppDialogProps {
  domain: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** The application's `stop` verb (`useAppActions`): the dialog waits for it to settle, then closes. */
  stop: { mutate: (variables: undefined, options: { onSettled: () => void }) => void };
}

/**
 * The one question before an application is stopped (friction "simple": it can be started
 * again, nothing is deleted). The page's Stop button and the command palette's both draw this, so
 * the words and the weight of the question are the same wherever it is asked.
 */
export function StopAppDialog({ domain, open, onOpenChange, stop }: StopAppDialogProps) {
  const t = useT();
  const { node } = useNode();
  return (
    <ConfirmDialog
      open={open}
      onOpenChange={onOpenChange}
      friction="simple"
      server={node}
      title={t("appPages.actions.stopTitle", { domain })}
      description={t("appPages.actions.stopDescription")}
      actionLabel={t("appPages.actions.stopApplication")}
      // The outcome, success or failure, is the toast's: the dialog waits for it, then closes.
      onConfirm={() =>
        new Promise<void>((resolve) => {
          stop.mutate(undefined, {
            onSettled: () => {
              resolve();
            },
          });
        })
      }
    />
  );
}
