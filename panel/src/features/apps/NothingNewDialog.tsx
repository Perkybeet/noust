import type { ApiError } from "../../api/errors";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { useT } from "../../i18n";

export interface NothingNewDialogProps {
  domain: string;
  /** The `409 nothing_new` the update was answered with; the dialog is open while it is set. */
  refusal: ApiError | null;
  /** The forced retry is in flight. */
  pending: boolean;
  /** Retries the update with `force`. */
  onRebuild: () => void;
  onClose: () => void;
}

/**
 * The question an update without anything new asks: the branch head is the commit that is
 * live, so updating would rebuild the same commit. The backend's own sentence says which
 * commit and which branch, and its hint when rebuilding still makes sense; nothing is
 * paraphrased.
 */
export function NothingNewDialog({ domain, refusal, pending, onRebuild, onClose }: NothingNewDialogProps) {
  const t = useT();
  return (
    <Dialog
      open={refusal !== null}
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
      size="sm"
      title={t("apps.nothingNew.title", { domain })}
      description={refusal?.detail}
      footer={
        <>
          <Button disabled={pending} onClick={onClose}>
            {t("apps.nothingNew.cancel")}
          </Button>
          <Button variant="primary" loading={pending} onClick={onRebuild}>
            {t("apps.nothingNew.rebuildAnyway")}
          </Button>
        </>
      }
    >
      {refusal?.hint ? <p className="text-13 text-pretty text-fg-muted">{refusal.hint}</p> : null}
    </Dialog>
  );
}
