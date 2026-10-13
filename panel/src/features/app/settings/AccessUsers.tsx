import { useMutation, useQueryClient } from "@tanstack/react-query";
import { KeyRound, Plus, Trash2 } from "lucide-react";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { isApiError, request } from "../../../api/client";
import { networkKeys, policyOf } from "../../../api/queries/network";
import type { AppPolicy } from "../../../api/queries/network";
import { ErrorBlock } from "../../../components/page/QueryState";
import { Button } from "../../../components/ui/Button";
import { Card } from "../../../components/ui/Card";
import { ConfirmDialog } from "../../../components/ui/ConfirmDialog";
import { Dialog } from "../../../components/ui/Dialog";
import { EmptyState } from "../../../components/ui/EmptyState";
import { Field } from "../../../components/ui/Field";
import { IconButton } from "../../../components/ui/IconButton";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { toast } from "../../../components/ui/toast";
import { useT } from "../../../i18n";
import { useNode } from "../../../nodes/useNode";

/** What the server accepts as a login name (`noust.managers.site_policy`). */
const USERNAME = /^[A-Za-z0-9._@-]{1,64}$/;

interface PasswordDialogProps {
  domain: string;
  /** The account to change, or null to add one. */
  name: string | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  onSaved: (policy: AppPolicy, name: string) => void;
}

/**
 * An account of the site's password: its name (when it is new) and its password, sent once and
 * hashed at the server. The password is never shown back, and nothing here keeps it after the
 * dialog closes.
 */
function PasswordDialog({ domain, name, open, onOpenChange, onSaved }: PasswordDialogProps) {
  const t = useT();
  const formId = useId();
  const [typed, setTyped] = useState("");
  const [password, setPassword] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const account = name ?? typed.trim();

  const save = useMutation({
    mutationFn: () =>
      request("put", "/api/apps/{domain}/policy/users/{username}", {
        params: { domain, username: account },
        body: { password },
      }),
    onSuccess: (policy) => {
      onSaved(policyOf(policy), account);
      close(false, true);
    },
  });

  function close(next: boolean, settled = false): void {
    if (!next && save.isPending && !settled) return;
    onOpenChange(next);
    if (!next) {
      setTyped("");
      setPassword("");
      setProblem(null);
      save.reset();
    }
  }

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (name === null && !USERNAME.test(account)) {
      setProblem(t("network.access.users.nameHelp"));
      return;
    }
    setProblem(null);
    save.mutate();
  };

  const serverField = save.error && isApiError(save.error) ? save.error.fields : undefined;
  const nameError = problem ?? serverField?.["username"] ?? null;
  const passwordError = serverField?.["password"] ?? null;
  const placed = nameError !== null || passwordError !== null;

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      title={name === null ? t("network.access.users.addTitle") : t("network.access.users.changeTitle", { name })}
      description={name === null ? t("network.access.users.addDescription", { domain }) : t("network.access.users.changeDescription")}
      footer={
        <>
          <Button disabled={save.isPending} onClick={() => close(false)}>
            {t("common.confirmDialog.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={save.isPending}>
            {name === null ? t("network.access.users.add") : t("network.access.users.change")}
          </Button>
        </>
      }
    >
      <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
        {name === null ? (
          <Field label={t("network.access.users.nameLabel")} description={t("network.access.users.nameHelp")} error={nameError}>
            <Input mono value={typed} onValueChange={setTyped} autoComplete="off" autoCapitalize="off" spellCheck={false} maxLength={64} />
          </Field>
        ) : null}
        <Field label={t("network.access.users.passwordLabel")} description={t("network.access.users.passwordHelp")} error={passwordError}>
          <Input type="password" value={password} onValueChange={setPassword} autoComplete="new-password" maxLength={256} />
        </Field>
        {save.isError && !placed ? <ErrorBlock live compact error={save.error} title={t("network.access.users.addFailed", { name: account })} /> : null}
      </form>
    </Dialog>
  );
}

/**
 * The accounts that can sign in to a site that asks for a password. They are applied at once,
 * each on its own, because a password is not something to leave half-typed in a form: adding,
 * changing and removing one saves it (hashed) and renders the site again.
 */
export function AccessUsers({ domain, policy }: { domain: string; policy: AppPolicy }) {
  const t = useT();
  const queryClient = useQueryClient();
  const { node } = useNode();
  const [dialog, setDialog] = useState<{ name: string | null } | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);

  const remember = (next: AppPolicy): void => {
    queryClient.setQueryData(networkKeys.policy(domain), next);
  };

  const onlyAccount = policy.users.length === 1 && policy.policy.basic_auth;

  return (
    <Card
      title={t("network.access.users.title")}
      description={t("network.access.users.description")}
      actions={
        <Button size="sm" icon={<Plus aria-hidden="true" />} onClick={() => setDialog({ name: null })}>
          {t("network.access.users.add")}
        </Button>
      }
    >
      {policy.users.length === 0 ? (
        <EmptyState variant="inline" title={t("network.access.users.empty")} />
      ) : (
        <ul aria-label={t("network.access.users.listLabel", { domain })} className="flex flex-col divide-y divide-border rounded-control border border-border">
          {policy.users.map((name) => (
            <li key={name} className="flex min-w-0 items-center justify-between gap-3 px-3 py-2">
              <Mono truncate>{name}</Mono>
              <span className="flex shrink-0 items-center gap-1">
                <IconButton label={`${t("network.access.users.change")}: ${name}`} icon={<KeyRound />} size="sm" onClick={() => setDialog({ name })} />
                <IconButton label={`${t("network.access.users.remove")}: ${name}`} icon={<Trash2 />} size="sm" onClick={() => setRemoving(name)} />
              </span>
            </li>
          ))}
        </ul>
      )}
      <PasswordDialog
        domain={domain}
        name={dialog?.name ?? null}
        open={dialog !== null}
        onOpenChange={(open) => {
          if (!open) setDialog(null);
        }}
        onSaved={(next, name) => {
          remember(next);
          toast.success(t("network.access.users.saved", { name, domain }));
        }}
      />
      <ConfirmDialog
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        friction="simple"
        server={node}
        title={t("network.access.users.removeTitle", { name: removing ?? "" })}
        description={
          <>
            {t("network.access.users.removeDescription", { name: removing ?? "", domain })}
            {onlyAccount ? <> {t("network.access.users.removeLast")}</> : null}
          </>
        }
        actionLabel={t("network.access.users.remove")}
        destructive
        onConfirm={async () => {
          if (removing === null) return;
          const next = await request("delete", "/api/apps/{domain}/policy/users/{username}", { params: { domain, username: removing } });
          remember(policyOf(next));
          toast.success(t("network.access.users.removed", { name: removing }));
        }}
      />
    </Card>
  );
}
