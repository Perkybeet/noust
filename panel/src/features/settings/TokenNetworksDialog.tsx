import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { authKeys, restrictApiToken } from "../../api/queries/auth";
import type { ApiToken } from "../../api/queries/auth";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Textarea } from "../../components/ui/Textarea";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import { parseCidrs } from "./CreateTokenDialog";
import { splitErrors } from "./formErrors";

const FIELDS = ["allowed_cidrs"] as const;

export interface TokenNetworksDialogProps {
  token: ApiToken;
  onClose: () => void;
}

/**
 * The addresses a live token is accepted from, changed without issuing another: what the
 * script that uses it keeps working with. Taking addresses away is immediate; widening asks to
 * confirm it is you, which the client does when the server says so.
 */
export function TokenNetworksDialog({ token, onClose }: TokenNetworksDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [text, setText] = useState((token.allowed_cidrs ?? []).join("\n"));
  const formId = useId();
  const save = useMutation({
    mutationFn: () => restrictApiToken(token.id, parseCidrs(text)),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: authKeys.tokens });
      toast.success(t("accounts.tokens.networks.savedToast", { name: token.name }));
      onClose();
    },
  });
  const errors = splitErrors(save.error, FIELDS);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (save.isPending) return;
    save.mutate();
  };

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        // Pending includes "Confirm it's you", which opens over this dialog.
        if (!next && !save.isPending) onClose();
      }}
      size="md"
      title={t("accounts.tokens.networks.title", { name: token.name })}
      description={t("accounts.tokens.networks.description")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={onClose}>
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {t("accounts.tokens.networks.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("accounts.tokens.networks.errorTitle")} /> : null}
        <Field label={t("accounts.tokens.addressesLabel")} optional description={t("accounts.tokens.addressesHint")} error={errors.fields.allowed_cidrs}>
          <Textarea
            mono
            rows={3}
            spellCheck={false}
            placeholder="203.0.113.0/24"
            value={text}
            onChange={(event) => {
              setText(event.target.value);
            }}
          />
        </Field>
      </form>
    </Dialog>
  );
}
