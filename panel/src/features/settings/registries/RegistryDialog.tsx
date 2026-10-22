import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { changeRegistryLogin, createRegistryLogin, registryKeys } from "../../../api/queries/registries";
import type { RegistryLogin, RegistryLoginChangeBody } from "../../../api/queries/registries";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Select } from "../../../components/ui/Select";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { splitErrors } from "../formErrors";
import { kindOptions, needsServer, passwordLabelKey, usernameLabelKey } from "./registries";

const FIELDS = ["name", "kind", "server", "username", "password"] as const;

export interface RegistryDialogProps {
  open: boolean;
  /** The account being edited; null adds a new one. */
  login: RegistryLogin | null;
  kinds: readonly string[];
  onClose: () => void;
}

/**
 * Adding a registry account, or changing one. The password is only ever sent: an edit that
 * leaves it empty keeps the stored one, and what the server refuses is shown beside its field.
 */
export function RegistryDialog({ open, login, kinds, onClose }: RegistryDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const formId = useId();
  const firstRef = useRef<HTMLInputElement>(null);
  const editing = login !== null;
  const [name, setName] = useState(login?.name ?? "");
  const [kind, setKind] = useState(login?.kind ?? kinds[0] ?? "dockerhub");
  const [server, setServer] = useState(login !== null && needsServer(login.kind) ? login.server : "");
  const [username, setUsername] = useState(login?.username ?? "");
  const [password, setPassword] = useState("");

  const save = useMutation({
    mutationFn: async () => {
      if (login === null) {
        return createRegistryLogin({
          name: name.trim(),
          kind,
          username: username.trim(),
          password,
          ...(server.trim() === "" ? {} : { server: server.trim() }),
        });
      }
      const change: RegistryLoginChangeBody = {
        ...(kind === login.kind ? {} : { kind }),
        ...(username.trim() === login.username ? {} : { username: username.trim() }),
        ...(server.trim() === "" || server.trim() === login.server ? {} : { server: server.trim() }),
        ...(password === "" ? {} : { password }),
      };
      return changeRegistryLogin(login.name, change);
    },
    onSuccess: (saved) => {
      toast.success(editing ? t("registries.dialog.saved", { name: saved.name }) : t("registries.dialog.added", { name: saved.name }));
      void queryClient.invalidateQueries({ queryKey: registryKeys.all });
      onClose();
    },
  });
  const errors = splitErrors(save.error, FIELDS);

  const close = (): void => {
    if (!save.isPending) onClose();
  };
  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!save.isPending) save.mutate();
  };
  const ready = editing ? true : name.trim() !== "" && username.trim() !== "" && password !== "" && (!needsServer(kind) || server.trim() !== "");

  return (
    <Dialog
      open={open}
      onOpenChange={(next) => {
        if (!next) close();
      }}
      size="md"
      initialFocus={firstRef}
      title={editing ? t("common.words.editNamed", { name: login.name }) : t("registries.dialog.addTitle")}
      description={editing ? t("registries.dialog.editDescription") : t("registries.dialog.addDescription")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={close}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending} disabled={!ready}>
            {editing ? t("registries.dialog.saveEdit") : t("registries.dialog.save")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("registries.dialog.failed")} /> : null}
        {editing ? null : (
          <Field label={t("common.words.name")} description={t("registries.dialog.nameDescription")} error={errors.fields.name}>
            <Input ref={firstRef} mono autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={63} value={name} onValueChange={setName} />
          </Field>
        )}
        <Field label={t("registries.dialog.kind")} nativeLabel={false} error={errors.fields.kind}>
          <Select options={kindOptions(t, kinds)} value={kind} onValueChange={setKind} />
        </Field>
        <Field
          label={t("registries.dialog.server")}
          optional={!needsServer(kind)}
          description={needsServer(kind) ? t("registries.dialog.serverRequired") : t("registries.dialog.serverDescription")}
          error={errors.fields.server}
        >
          <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={300} value={server} onValueChange={setServer} />
        </Field>
        <Field label={t(usernameLabelKey(kind))} error={errors.fields.username}>
          <Input ref={editing ? firstRef : undefined} mono autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={256} value={username} onValueChange={setUsername} />
        </Field>
        <Field label={editing ? t("registries.dialog.passwordKeep") : t(passwordLabelKey(kind))} error={errors.fields.password}>
          <Input mono type="password" autoComplete="new-password" spellCheck={false} maxLength={8192} value={password} onValueChange={setPassword} />
        </Field>
      </form>
    </Dialog>
  );
}
