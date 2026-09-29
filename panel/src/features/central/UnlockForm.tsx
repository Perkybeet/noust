import { useQueryClient } from "@tanstack/react-query";
import { LockOpen } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { ElevationCancelledError } from "../../api/client";
import { announce } from "../../app/Announcer";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { isWrongPassphrase, unlockCentral } from "./central";

export interface UnlockFormProps {
  /** Called once the central is unlocked. */
  onUnlocked?: () => void;
  /** Focus the passphrase on mount: the lock screen's one task. */
  focusOnMount?: boolean;
  /** Renders the submit button elsewhere (a dialog's footer): the form's id. */
  formId?: string;
  /** Hides the form's own submit button, for a dialog that has one in its footer. */
  hideSubmit?: boolean;
  onPendingChange?: (pending: boolean) => void;
}

/**
 * The passphrase that unseals the central's secrets. A wrong one is said at the field; any
 * other refusal (another unlock holding the seal, openssl failing) is shown in the system's
 * own words with its fix.
 */
export function UnlockForm({ onUnlocked, focusOnMount = false, formId, hideSubmit = false, onPendingChange }: UnlockFormProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [passphrase, setPassphrase] = useState("");
  const [pending, setPending] = useState(false);
  const [wrong, setWrong] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);
  const inputRef = useRef<HTMLInputElement>(null);

  useEffect(() => {
    if (focusOnMount) inputRef.current?.focus();
  }, [focusOnMount]);

  // A refused passphrase is selected for retyping once the field is enabled again.
  useEffect(() => {
    if (pending || !wrong) return;
    inputRef.current?.focus();
    inputRef.current?.select();
  }, [pending, wrong]);

  const submit = async (event: SyntheticEvent<HTMLFormElement>): Promise<void> => {
    event.preventDefault();
    if (pending || passphrase === "") return;
    setPending(true);
    onPendingChange?.(true);
    setWrong(false);
    setFailure(null);
    try {
      await unlockCentral(queryClient, passphrase);
      setPassphrase("");
      toast.success(t("servers.lock.unlockedToast"));
      onUnlocked?.();
    } catch (error: unknown) {
      // Declining "Confirm it's you" is not a refusal: nothing was tried.
      if (error instanceof ElevationCancelledError) return;
      if (isWrongPassphrase(error)) {
        setWrong(true);
        announce(t("servers.lock.wrongPassphrase"), "assertive");
      } else {
        setFailure(error);
      }
    } finally {
      setPending(false);
      onPendingChange?.(false);
    }
  };

  return (
    <form id={formId} noValidate onSubmit={(event) => void submit(event)} className="flex flex-col gap-4">
      {failure !== null ? <ErrorBlock live compact error={failure} title={t("servers.lock.failedTitle")} /> : null}
      <Field label={t("servers.lock.passphraseLabel")} error={wrong ? t("servers.lock.wrongPassphrase") : null}>
        <Input
          ref={inputRef}
          type="password"
          autoComplete="current-password"
          autoCapitalize="off"
          spellCheck={false}
          value={passphrase}
          disabled={pending}
          onValueChange={(value: string) => {
            setPassphrase(value);
            if (wrong) setWrong(false);
          }}
        />
      </Field>
      {hideSubmit ? null : (
        <Button type="submit" variant="primary" size="lg" loading={pending} disabled={passphrase === ""} icon={<LockOpen aria-hidden="true" />}>
          {t("servers.lock.unlock")}
        </Button>
      )}
    </form>
  );
}
