import type { ApiError } from "../../api/errors";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { useT } from "../../i18n";

export interface FrozenDeployDialogProps {
  domain: string;
  /** The `409 deploys_frozen` the deploy was answered with; the dialog is open while it is set. */
  refusal: ApiError | null;
  /** The deploy that goes ahead anyway is in flight. */
  pending: boolean;
  /** Asks again, saying it must go ahead. */
  onDeploy: () => void;
  onClose: () => void;
}

/**
 * The question a deploy during a freeze asks: somebody froze deploys and said why, and the
 * backend's own sentence (who, until when, the reason) is shown verbatim. Going ahead is a
 * choice, recorded in the audit log, for the person who knows the freeze does not apply.
 */
export function FrozenDeployDialog({ domain, refusal, pending, onDeploy, onClose }: FrozenDeployDialogProps) {
  const t = useT();
  return (
    <Dialog
      open={refusal !== null}
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
      size="sm"
      title={t("rollout.frozenDeploy.title", { domain })}
      description={refusal?.detail}
      footer={
        <>
          <Button disabled={pending} onClick={onClose}>
            {t("rollout.frozenDeploy.cancel")}
          </Button>
          <Button variant="primary" loading={pending} onClick={onDeploy}>
            {t("rollout.frozenDeploy.anyway")}
          </Button>
        </>
      }
    >
      <div className="flex flex-col gap-2">
        {refusal?.hint ? <p className="text-13 text-pretty text-fg-muted">{refusal.hint}</p> : null}
        <p className="text-13 text-pretty text-fg-muted">{t("rollout.frozenDeploy.note")}</p>
      </div>
    </Dialog>
  );
}
