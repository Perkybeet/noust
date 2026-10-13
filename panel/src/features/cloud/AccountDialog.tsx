import { useQuery } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { ExternalLink } from "../../components/ui/ExternalLink";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { SecretInput } from "../settings/channelParts";
import { ACCOUNT_NAME, PROVIDER_KINDS, cloudProvidersQuery, plausibleToken } from "./data";
import type { CloudAccount, ProviderKind } from "./data";
import { useCloudActions } from "./useCloudActions";

export interface AccountDialogProps {
  /** Present to replace an account's token; absent to add an account. */
  existing?: CloudAccount;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

function isKind(value: string): value is ProviderKind {
  return (PROVIDER_KINDS as readonly string[]).includes(value);
}

/**
 * Adds a provider account, or replaces the token of one. The token is asked once, shown only if
 * the operator reveals it, checked with the provider before it is kept, and gone from the page the
 * moment the request leaves: nothing the console shows afterwards carries more than its last four
 * characters. Confirming asks "Confirm it's you".
 */
export function AccountDialog({ existing, open, onOpenChange }: AccountDialogProps) {
  const t = useT();
  const formId = useId();
  const providers = useQuery({ ...cloudProvidersQuery(), enabled: open });
  const { addAccount, replaceToken } = useCloudActions();
  const nameRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState(existing?.name ?? "");
  const [provider, setProvider] = useState<string>(existing?.provider ?? "");
  const [token, setToken] = useState("");
  const [note, setNote] = useState(existing?.note ?? "");
  const [tried, setTried] = useState(false);

  const mutation = existing === undefined ? addAccount : replaceToken;
  const saving = mutation.isPending;

  const nameOk = existing !== undefined || ACCOUNT_NAME.test(name.trim());
  const providerOk = isKind(provider);
  const tokenOk = plausibleToken(token);
  const chosen = providers.data?.find((candidate) => candidate.kind === provider);

  const reset = (): void => {
    setName(existing?.name ?? "");
    setProvider(existing?.provider ?? "");
    setToken("");
    setNote(existing?.note ?? "");
    setTried(false);
    addAccount.reset();
    replaceToken.reset();
  };

  const close = (next: boolean): void => {
    if (!next && saving) return;
    onOpenChange(next);
    if (!next) reset();
  };

  const submit = (event: SyntheticEvent): void => {
    event.preventDefault();
    setTried(true);
    if (!nameOk || !providerOk || !tokenOk || saving) return;
    const done = (): void => {
      close(false);
    };
    if (existing === undefined) {
      addAccount.mutate({ name: name.trim(), provider, token: token.trim(), note: note.trim() }, { onSuccess: done });
    } else {
      replaceToken.mutate({ name: existing.name, token: token.trim() }, { onSuccess: done });
    }
    // The request has left: nothing of the token stays on this page, whatever the answer.
    setToken("");
  };

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="md"
      initialFocus={nameRef}
      title={existing === undefined ? t("cloud.account.addTitle") : t("cloud.account.replaceTitle", { name: existing.name })}
      description={existing === undefined ? t("cloud.account.addDescription") : t("cloud.account.replaceDescription")}
      footer={
        <>
          <Button
            onClick={() => {
              close(false);
            }}
          >
            {t("cloud.common.cancel")}
          </Button>
          <Button variant="primary" type="submit" form={formId} loading={saving}>
            {existing === undefined ? t("cloud.account.add") : t("cloud.account.replace")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate className="flex min-w-0 flex-col gap-5" onSubmit={submit}>
        {existing === undefined ? (
          <>
            <Field label={t("cloud.account.name")} description={t("cloud.account.nameHelp")} error={tried && !nameOk ? t("cloud.account.nameInvalid") : undefined}>
              <Input ref={nameRef} mono maxLength={32} value={name} onValueChange={(value: string) => setName(value)} placeholder="hetzner-prod" autoComplete="off" autoCapitalize="off" spellCheck={false} />
            </Field>
            <Field label={t("cloud.account.provider")} nativeLabel={false} error={tried && !providerOk ? t("cloud.account.providerRequired") : undefined}>
              <Select
                value={provider === "" ? null : provider}
                onValueChange={setProvider}
                placeholder={t("cloud.account.providerPlaceholder")}
                options={PROVIDER_KINDS.map((kind) => ({ value: kind, label: t(`cloud.provider.${kind}.name`) }))}
              />
            </Field>
          </>
        ) : null}
        <Field
          label={t("cloud.account.token")}
          description={providerOk ? t(`cloud.provider.${provider}.tokenHelp`) : t("cloud.account.tokenHelp")}
          error={tried && !tokenOk ? t("cloud.account.tokenInvalid") : undefined}
        >
          <SecretInput label={t("cloud.account.token")} placeholder={t("cloud.account.tokenPlaceholder")} value={token} configured={false} onChange={setToken} disabled={saving} />
        </Field>
        {chosen !== undefined && providerOk ? (
          <ExternalLink href={chosen.token_url}>{t("cloud.account.whereToken", { provider: t(`cloud.provider.${provider}.name`) })}</ExternalLink>
        ) : null}
        {existing === undefined ? (
          <Field label={t("cloud.account.note")} description={t("cloud.account.noteHelp")} optional>
            <Input maxLength={200} value={note} onValueChange={(value: string) => setNote(value)} autoComplete="off" />
          </Field>
        ) : null}
        <p className="text-13 text-pretty text-fg-muted">{t("cloud.account.kept")}</p>
        {mutation.isError ? <ErrorBlock live compact error={mutation.error} title={t("cloud.account.failed")} /> : null}
      </form>
    </Dialog>
  );
}
