import { useEffect, useRef, useState } from "react";

import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { CopyTextButton } from "../../components/ui/CopyTextButton";
import { Dialog } from "../../components/ui/Dialog";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { useDestinationActions } from "./useDestinationActions";

export interface ShowKeyDialogProps {
  /** Destination whose encryption passphrases are revealed. */
  name: string;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  /**
   * Shown as the first step of removing the destination: the key is what reads the backups left
   * behind, and the removal deletes Noust's copy. Cancelling is always allowed; continuing needs
   * "I have saved it" ticked, and calls this.
   */
  onContinueToRemove?: () => void;
}

/**
 * A destination's encryption passphrases, fetched fresh every time this opens (the server
 * keeps them; this is not a one-time generation like a two-factor backup code). Closing is
 * gated on ticking "I have saved it", the same pattern `TwoFactorSection`'s backup codes use,
 * because losing them makes every backup on that destination unrecoverable.
 */
export function ShowKeyDialog({ name, open, onOpenChange, onContinueToRemove }: ShowKeyDialogProps) {
  const t = useT();
  const removing = onContinueToRemove !== undefined;
  const { showKey } = useDestinationActions();
  const [saved, setSaved] = useState(false);
  const [nudge, setNudge] = useState(false);
  const savedRef = useRef<HTMLDivElement>(null);
  const fetchedFor = useRef<string | null>(null);

  useEffect(() => {
    if (!open) {
      fetchedFor.current = null;
      return;
    }
    if (fetchedFor.current === name) return;
    fetchedFor.current = name;
    showKey.mutate(name);
    // showKey is a fresh useMutation instance on every render; only `open` and `name` decide
    // when to fetch again.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [open, name]);

  const close = (next: boolean): void => {
    if (next) return;
    if (!removing && showKey.data !== undefined && !saved) {
      setNudge(true);
      savedRef.current?.querySelector<HTMLElement>("[role=checkbox]")?.focus();
      return;
    }
    onOpenChange(false);
    setSaved(false);
    setNudge(false);
    showKey.reset();
  };

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="sm"
      title={removing ? t("backups.showKeyDialog.titleRemove", { name }) : t("backups.showKeyDialog.titleShow", { name })}
      description={removing ? t("backups.showKeyDialog.descriptionRemove", { name }) : t("backups.showKeyDialog.descriptionShow")}
      footer={
        removing ? (
          <>
            <Button onClick={() => close(false)}>{t("backups.common.cancel")}</Button>
            <Button
              variant="primary"
              disabled={showKey.data === undefined || !saved}
              onClick={() => {
                close(false);
                onContinueToRemove();
              }}
            >
              {t("backups.showKeyDialog.continueToRemove")}
            </Button>
          </>
        ) : (
          <Button variant="primary" disabled={showKey.data === undefined || !saved} onClick={() => close(false)}>
            {t("backups.showKeyDialog.done")}
          </Button>
        )
      }
    >
      <div className="flex flex-col gap-4">
        {showKey.isPending || showKey.isIdle ? (
          <div aria-hidden="true" className="flex flex-col gap-2">
            <Skeleton className="h-8 rounded-control" />
            <Skeleton className="h-8 rounded-control" />
          </div>
        ) : showKey.isError ? (
          <ErrorBlock compact error={showKey.error} title={t("backups.showKeyDialog.error")} />
        ) : (
          <>
            <dl className="flex flex-col gap-2 rounded-control border border-border bg-bg-sunken px-4 py-3">
              <div className="flex flex-col gap-1">
                <dt className="text-12 text-fg-muted">{t("backups.showKeyDialog.passwordLabel")}</dt>
                <dd className="text-13 break-all select-all">
                  <Mono>{showKey.data.password}</Mono>
                </dd>
              </div>
              <div className="flex flex-col gap-1">
                <dt className="text-12 text-fg-muted">{t("backups.showKeyDialog.password2Label")}</dt>
                <dd className="text-13 break-all select-all">
                  <Mono>{showKey.data.password2}</Mono>
                </dd>
              </div>
            </dl>
            <div>
              <CopyTextButton value={`${showKey.data.password}\n${showKey.data.password2}`} size="sm">
                {t("backups.showKeyDialog.copyBoth")}
              </CopyTextButton>
            </div>
            <div ref={savedRef} className="flex flex-col gap-3">
              <Checkbox label={t("backups.showKeyDialog.savedLabel")} checked={saved} onCheckedChange={setSaved} />
              {/* Said once, when closing was refused: the operator's own action, so announced. */}
              {nudge ? (
                <Notice tone="warning" live>
                  {t("backups.showKeyDialog.nudge")}
                </Notice>
              ) : null}
            </div>
          </>
        )}
      </div>
    </Dialog>
  );
}
