import { LockOpen } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Notice } from "../../components/ui/Notice";
import { useT } from "../../i18n";
import { useCentral } from "./central";
import { UnlockForm } from "./UnlockForm";

/** "Unlock this central": the passphrase form in a dialog, from any page that needs the servers. */
export function UnlockDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
  const t = useT();
  const formId = useId();
  const [pending, setPending] = useState(false);
  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next && pending) return;
        onOpenChange(next);
      }}
      size="sm"
      title={t("servers.lock.dialogTitle")}
      description={t("servers.lock.description")}
      footer={
        <>
          <Button
            disabled={pending}
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={pending} icon={<LockOpen aria-hidden="true" />}>
            {t("servers.lock.unlock")}
          </Button>
        </>
      }
    >
      <UnlockForm
        formId={formId}
        hideSubmit
        onPendingChange={setPending}
        onUnlocked={() => {
          onOpenChange(false);
        }}
      />
    </Dialog>
  );
}

/**
 * Says, on a page about the servers, that the central is locked and nothing reaches them,
 * with the way to unlock it. Renders nothing on an unlocked central.
 */
export function CentralLockedNotice({ className }: { className?: string }) {
  const t = useT();
  const central = useCentral();
  const [open, setOpen] = useState(false);
  if (!central.locked) return null;
  return (
    <>
      <Notice
        tone="warning"
        variant="banner"
        title={t("servers.lock.bannerTitle")}
        action={
          <Button
            size="sm"
            icon={<LockOpen aria-hidden="true" />}
            onClick={() => {
              setOpen(true);
            }}
          >
            {t("servers.lock.bannerAction")}
          </Button>
        }
        {...(className !== undefined ? { className } : {})}
      >
        {t("servers.lock.bannerDescription")}
      </Notice>
      <UnlockDialog open={open} onOpenChange={setOpen} />
    </>
  );
}
