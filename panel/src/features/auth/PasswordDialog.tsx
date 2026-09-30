import { useMutation } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { request } from "../../api/client";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { splitErrors } from "../settings/formErrors";

const FIELDS = ["current_password", "new_password"] as const;

/**
 * Changing one's own password: the current one, then the new one twice. The policy (length,
 * common passwords, the name inside it, the last few used) is the server's, and its words are
 * shown beside the field they are about.
 */
export function PasswordDialog({ onClose }: { onClose: () => void }) {
  const t = useT();
  const [current, setCurrent] = useState("");
  const [next, setNext] = useState("");
  const [again, setAgain] = useState("");
  const [mismatch, setMismatch] = useState(false);
  const currentRef = useRef<HTMLInputElement>(null);
  const formId = useId();
  const change = useMutation({
    mutationFn: () => request("post", "/api/auth/password", { body: { current_password: current, new_password: next } }),
    onSuccess: () => {
      toast.success(t("auth.password.changedToast"));
      onClose();
    },
  });
  const errors = splitErrors(change.error, FIELDS);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (change.isPending) return;
    if (next !== again) {
      setMismatch(true);
      return;
    }
    setMismatch(false);
    change.mutate();
  };

  return (
    <Dialog
      open
      onOpenChange={(open) => {
        if (!open && !change.isPending) onClose();
      }}
      size="sm"
      initialFocus={currentRef}
      title={t("auth.password.title")}
      description={t("auth.password.description")}
      footer={
        <>
          <Button disabled={change.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={change.isPending} disabled={current === "" || next === "" || again === ""}>
            {t("auth.password.submit")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("auth.password.failed")} /> : null}
        <Field label={t("auth.password.current")} error={errors.fields.current_password}>
          <Input
            ref={currentRef}
            type="password"
            autoComplete="current-password"
            value={current}
            onValueChange={(value: string) => {
              setCurrent(value);
            }}
          />
        </Field>
        <Field label={t("auth.password.new")} error={errors.fields.new_password}>
          <Input
            type="password"
            autoComplete="new-password"
            value={next}
            onValueChange={(value: string) => {
              setNext(value);
            }}
          />
        </Field>
        <Field label={t("auth.password.again")} error={mismatch ? t("auth.invite.mismatch") : null}>
          <Input
            type="password"
            autoComplete="new-password"
            value={again}
            onValueChange={(value: string) => {
              setAgain(value);
            }}
          />
        </Field>
      </form>
    </Dialog>
  );
}
