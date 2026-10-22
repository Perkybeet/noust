import { useMutation } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { useT } from "../../../i18n";
import { passphraseProblem } from "./data";

export interface PassphraseDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description: string;
  submit: string;
  /** Asks for the passphrase twice: a bundle sealed under a mistyped one cannot be read. */
  repeat: boolean;
  /** Starts the job with the passphrase; it is handed over and forgotten, never kept here. */
  onSubmit: (passphrase: string) => Promise<void>;
}

/**
 * The passphrase a bundle is sealed under or checked with, asked for when the server does not
 * keep one. It is a password field, typed twice when it seals, sent once in the request that
 * starts the job and forgotten when the dialog closes.
 */
export function PassphraseDialog({ open, onOpenChange, title, description, submit, repeat, onSubmit }: PassphraseDialogProps) {
  const t = useT();
  const formId = useId();
  const [passphrase, setPassphrase] = useState("");
  const [again, setAgain] = useState("");
  const [error, setError] = useState<{ field: "passphrase" | "repeat"; text: string } | null>(null);
  const start = useMutation({ mutationFn: (value: string) => onSubmit(value) });

  const close = (next: boolean): void => {
    if (!next && start.isPending) return;
    onOpenChange(next);
    if (!next) {
      setPassphrase("");
      setAgain("");
      setError(null);
      start.reset();
    }
  };

  const send = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    const problem = passphraseProblem(passphrase, repeat ? again : null);
    if (problem !== null) {
      setError(problem === "mismatch" ? { field: "repeat", text: t("selfBackup.passphrase.mismatch") } : { field: "passphrase", text: t(problem === "short" ? "selfBackup.passphrase.short" : "selfBackup.passphrase.long") });
      return;
    }
    setError(null);
    start.mutate(passphrase, { onSuccess: () => close(false) });
  };

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="md"
      title={title}
      description={description}
      footer={
        <>
          <Button disabled={start.isPending} onClick={() => close(false)}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={start.isPending}>
            {submit}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={send} className="flex flex-col gap-5">
        <Field label={t("selfBackup.passphrase.label")} description={repeat ? t("selfBackup.passphrase.help") : undefined} error={error?.field === "passphrase" ? error.text : undefined}>
          <Input mono type="password" value={passphrase} onValueChange={(value: string) => setPassphrase(value)} autoComplete="new-password" autoCapitalize="off" spellCheck={false} />
        </Field>
        {repeat ? (
          <Field label={t("selfBackup.passphrase.repeat")} description={t("selfBackup.passphrase.repeatHelp")} error={error?.field === "repeat" ? error.text : undefined}>
            <Input mono type="password" value={again} onValueChange={(value: string) => setAgain(value)} autoComplete="new-password" autoCapitalize="off" spellCheck={false} />
          </Field>
        ) : null}
        {start.isError ? <ErrorBlock live compact error={start.error} title={t("selfBackup.passphrase.failed")} /> : null}
      </form>
    </Dialog>
  );
}
