import { LockKeyhole, LockOpen } from "lucide-react";
import { useId, useState } from "react";

import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
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
    <div
      className={cx(
        "flex flex-wrap items-center justify-between gap-x-4 gap-y-3 rounded-card border border-warn/40 bg-warn-soft px-4 py-3",
        className,
      )}
    >
      <div className="flex min-w-0 items-start gap-2.5">
        <LockKeyhole aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
        <div className="flex min-w-0 flex-col gap-0.5">
          <p className="text-13 font-medium text-fg">{t("servers.lock.bannerTitle")}</p>
          <p className="text-13 text-pretty text-fg-muted">{t("servers.lock.bannerDescription")}</p>
        </div>
      </div>
      <Button
        size="sm"
        icon={<LockOpen aria-hidden="true" />}
        onClick={() => {
          setOpen(true);
        }}
      >
        {t("servers.lock.bannerAction")}
      </Button>
      <UnlockDialog open={open} onOpenChange={setOpen} />
    </div>
  );
}
