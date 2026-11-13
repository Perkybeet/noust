import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { useT } from "../../i18n";
import { PlanDisclosure } from "../plans/PlanDisclosure";

export interface UpdatePlanDialogProps {
  domain: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /** Updates it: what the header's Update does, once the person has seen what it would do. */
  onUpdate: () => void;
}

/**
 * "See what it will do" before an update (item 183): Noust's own simulation of what pressing Update would run, read when this opens and
 * open from the start, because looking at it is the whole point of the dialog. Updating stays one press in the header; this is the way to
 * look first, and it updates when the person decides to. It runs nothing and changes nothing until then.
 */
export function UpdatePlanDialog({ domain, open, onOpenChange, onUpdate }: UpdatePlanDialogProps) {
  const t = useT();
  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="lg"
      title={t("plans.update.title", { domain })}
      description={t("plans.update.description")}
      footer={
        <>
          <Button onClick={() => onOpenChange(false)}>{t("common.words.cancel")}</Button>
          <Button
            variant="primary"
            onClick={() => {
              onOpenChange(false);
              onUpdate();
            }}
          >
            {t("common.words.update")}
          </Button>
        </>
      }
    >
      {open ? <PlanDisclosure operation="update" params={{ domain }} defaultOpen /> : null}
    </Dialog>
  );
}
