import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";
import { declinePassword, givePassword, usePasswordAsked } from "./identityProof";

/**
 * "Confirm your password": asked before a first second factor is set up from a session that signed in a while ago.
 * Mounted once, at the root, and opened by the API client when the server asks (403 identity_proof_required), which
 * then sends the call again with what was typed. A wrong password is the server's answer to the call, said where
 * the call was made, not another round of this dialog.
 */
export function IdentityProofDialog() {
  const t = useT();
  const open = usePasswordAsked();
  const [password, setPassword] = useState("");
  const [tried, setTried] = useState(false);
  const inputRef = useRef<HTMLInputElement>(null);
  const formId = useId();

  const reset = (): void => {
    setPassword("");
    setTried(false);
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (password === "") {
      setTried(true);
      inputRef.current?.focus();
      return;
    }
    const given = password;
    reset();
    givePassword(given);
  };

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (next) return;
        reset();
        declinePassword();
      }}
      size="sm"
      initialFocus={inputRef}
      title={t("auth.identityProof.title")}
      description={t("auth.identityProof.description")}
      footer={
        <>
          <Button
            onClick={() => {
              reset();
              declinePassword();
            }}
          >
            {t("auth.elevate.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary">
            {t("auth.elevate.confirm")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-4">
        <Field label={t("auth.account.password")} error={tried && password === "" ? t("auth.identityProof.required") : undefined}>
          <Input
            ref={inputRef}
            type="password"
            autoComplete="current-password"
            value={password}
            onValueChange={(next: string) => {
              setPassword(next);
            }}
          />
        </Field>
      </form>
    </Dialog>
  );
}
