import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { duplicateRole, rolesChanged } from "../../../api/queries/roles";
import type { Role } from "../../../api/queries/roles";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { useT } from "../../../i18n";
import { focusFirstInvalid, splitErrors } from "../formErrors";
import { roleLabel } from "./roleModel";

/** The name a copy of a role proposes: the role's own with "-copy", which the operator changes. */
export function copyName(name: string): string {
  return `${name}-copy`.slice(0, 40);
}

/** Copying a role into one of your own (item 96): the way to start from "viewer" and add one permission. */
export function DuplicateRoleDialog({ role, onClose }: { role: Role; onClose: () => void }) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const [name, setName] = useState(copyName(role.name));
  const [tried, setTried] = useState(false);
  const copy = useMutation({
    mutationFn: () => duplicateRole(role.name, { name: name.trim() }),
    onSuccess: () => {
      rolesChanged(queryClient);
      onClose();
    },
  });
  const errors = splitErrors(copy.error, ["name"] as const, "name");

  return (
    <Dialog
      open
      onOpenChange={(next) => {
        if (!next && !copy.isPending) onClose();
      }}
      size="sm"
      title={t("roles.duplicate.title", { role: roleLabel(t, role) })}
      description={t("roles.duplicate.description")}
      footer={
        <>
          <Button disabled={copy.isPending} onClick={onClose}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={copy.isPending}>
            {t("roles.duplicate.action")}
          </Button>
        </>
      }
    >
      <form
        id={formId}
        noValidate
        className="flex flex-col gap-4"
        onSubmit={(event: SyntheticEvent<HTMLFormElement>) => {
          event.preventDefault();
          if (copy.isPending) return;
          if (name.trim() === "") {
            setTried(true);
            focusFirstInvalid(formId);
            return;
          }
          copy.mutate();
        }}
      >
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("roles.duplicate.failed")} /> : null}
        <Field label={t("roles.duplicate.name")} description={t("roles.duplicate.nameHelp")} error={errors.fields.name ?? (tried && name.trim() === "" ? t("settings.shared.required") : undefined)}>
          <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={40} value={name} onValueChange={setName} />
        </Field>
      </form>
    </Dialog>
  );
}
