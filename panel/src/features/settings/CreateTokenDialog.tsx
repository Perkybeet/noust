import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { authKeys, createApiToken, sessionQuery } from "../../api/queries/auth";
import type { CreatedToken } from "../../api/queries/auth";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { CopyTextButton } from "../../components/ui/CopyTextButton";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Textarea } from "../../components/ui/Textarea";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import { rolesQuery } from "./accounts/api";
import type { RolesResponse } from "./accounts/api";
import { splitErrors } from "./formErrors";
import { DEFAULT_EXPIRY, expiryOptions, expiryPhrase, scopes } from "./tokens";
import type { TokenScope } from "./tokens";

export interface CreateTokenDialogProps {
  open: boolean;
  onClose: () => void;
}

const FIELDS = ["name", "scope", "expires_hours", "allowed_cidrs"] as const;

/**
 * What a token of each scope may do before its owner's cap: the server's TOKEN_SCOPES
 * (noust.web.permissions.roles), said here only to show it, from the roles it is made of.
 */
export function scopePermissions(roles: RolesResponse["roles"], scope: TokenScope): string[] {
  const viewer = roles["viewer"] ?? [];
  if (scope === "read") return [...viewer];
  if (scope === "deploy") return [...new Set([...viewer, "apps.deploy"])];
  return [...(roles["admin"] ?? [])];
}

/** The addresses a token is limited to, one per line or separated by commas. */
export function parseCidrs(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

/**
 * What the token will be able to do: its scope, capped by what its owner may do today. A
 * token never does more than the person it belongs to.
 */
function Capped({ t, scope, roles, own, owner }: { t: T; scope: TokenScope; roles: RolesResponse["roles"] | undefined; own: readonly string[]; owner: string | null }) {
  if (roles === undefined) return null;
  const wanted = scopePermissions(roles, scope).filter((permission) => permission !== "self");
  const kept = wanted.filter((permission) => own.includes(permission));
  const dropped = wanted.filter((permission) => !own.includes(permission));
  return (
    <Notice title={owner === null ? t("accounts.tokens.cappedMaster") : t("accounts.tokens.capped", { name: owner })}>
      <div className="flex flex-col gap-1.5">
        <p className="flex flex-wrap gap-x-2 gap-y-1">
          {kept.map((permission) => (
            <Mono key={permission} tone="default">
              {permission}
            </Mono>
          ))}
        </p>
        {dropped.length > 0 ? (
          <p className="text-fg-muted">{t("accounts.tokens.dropped", { permissions: dropped.join(", ") })}</p>
        ) : null}
      </div>
    </Notice>
  );
}

/** The token, once: copy it now, because only its hash is kept. */
function TokenOnce({ t, token }: { t: T; token: CreatedToken }) {
  const labelId = useId();
  const example = `curl -H "Authorization: Bearer ${token.token}" ${window.location.origin}/api/apps`;
  return (
    <div className="flex flex-col gap-4">
      <Notice tone="warning" live>
        {t("settings.tokens.once.warning")}
      </Notice>
      <div className="flex flex-col gap-1.5">
        <span id={labelId} className="text-13 font-medium text-fg">
          {t("settings.tokens.once.tokenLabel", { name: token.name })}
        </span>
        {/* All of it, wrapped: a token cut off by a narrow field cannot be checked by eye. */}
        <span aria-labelledby={labelId} data-testid="new-token" className="rounded-control border border-border-strong bg-surface px-3 py-2 text-13 break-all select-all">
          <Mono tone="default">{token.token}</Mono>
        </span>
      </div>
      <div>
        <CopyTextButton value={token.token} variant="primary">
          {t("settings.tokens.once.copyToken")}
        </CopyTextButton>
      </div>
      <CommandHint label={t("settings.tokens.once.tryIt")} command={example} />
    </div>
  );
}

/** Issuing an API token: a name, a scope and an expiry, then the token itself, once. */
export function CreateTokenDialog({ open, onClose }: CreateTokenDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [scope, setScope] = useState<TokenScope>("read");
  const [expiry, setExpiry] = useState(DEFAULT_EXPIRY);
  const [cidrs, setCidrs] = useState("");
  const [created, setCreated] = useState<CreatedToken | null>(null);
  const { data: session } = useQuery(sessionQuery());
  const roles = useQuery({ ...rolesQuery(), enabled: open });
  const nameRef = useRef<HTMLInputElement>(null);
  const formId = useId();

  const create = useMutation({
    mutationFn: () =>
      createApiToken({
        name: name.trim(),
        scope,
        expires_hours: expiryOptions(t).find((option) => option.value === expiry)?.hours ?? null,
        allow_elevated: false,
        ...(parseCidrs(cidrs).length > 0 ? { allowed_cidrs: parseCidrs(cidrs) } : {}),
      }),
    onSuccess: (token) => {
      setCreated(token);
      void queryClient.invalidateQueries({ queryKey: authKeys.tokens });
    },
  });
  const errors = splitErrors(create.error, FIELDS);

  const reset = (): void => {
    setName("");
    setScope("read");
    setExpiry(DEFAULT_EXPIRY);
    setCidrs("");
    setCreated(null);
    create.reset();
  };

  const onOpenChange = (next: boolean): void => {
    // Pending includes "Confirm it's you", which opens over this dialog; a press inside it is
    // outside this one and must not close it.
    if (next || create.isPending) return;
    if (created !== null) toast.success(t("settings.tokens.create.createdToast", { name: created.name }));
    onClose();
    reset();
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (create.isPending) return;
    create.mutate();
  };

  if (created !== null) {
    const scopeLabel = scopes(t).find((option) => option.value === created.scope)?.label ?? created.scope;
    return (
      <Dialog
        open={open}
        onOpenChange={onOpenChange}
        size="md"
        title={t("settings.tokens.once.title")}
        description={t("settings.tokens.once.description", {
          name: created.name,
          scope: scopeLabel,
          expiry: expiryPhrase(t, created.expires_at ?? null),
        })}
        footer={
          <Button
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("settings.shared.done")}
          </Button>
        }
      >
        <TokenOnce t={t} token={created} />
      </Dialog>
    );
  }

  return (
    <Dialog
      open={open}
      onOpenChange={onOpenChange}
      size="md"
      initialFocus={nameRef}
      title={t("settings.tokens.create.title")}
      description={t("settings.tokens.create.description")}
      footer={
        <>
          <Button
            disabled={create.isPending}
            onClick={() => {
              onOpenChange(false);
            }}
          >
            {t("settings.shared.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={create.isPending} disabled={name.trim() === ""}>
            {t("settings.tokens.createToken")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("settings.tokens.create.errorTitle")} /> : null}
        <Field label={t("settings.tokens.create.nameLabel")} description={t("settings.tokens.create.nameDescription")} error={errors.fields.name}>
          <Input
            ref={nameRef}
            mono
            autoComplete="off"
            autoCapitalize="off"
            spellCheck={false}
            maxLength={64}
            value={name}
            onValueChange={(value: string) => {
              setName(value);
            }}
          />
        </Field>
        <ChoiceCards
          legend={t("settings.tokens.create.scopeLegend")}
          options={scopes(t).map((option) => ({ value: option.value, label: option.label, description: option.description }))}
          value={scope}
          onValueChange={setScope}
        />
        {errors.fields.scope !== undefined ? <p className="text-13 text-fail">{errors.fields.scope}</p> : null}
        <Capped t={t} scope={scope} roles={roles.data?.roles} own={session?.permissions ?? []} owner={session?.account?.username ?? null} />
        <Field label={t("settings.tokens.create.expiresLabel")} nativeLabel={false} error={errors.fields.expires_hours}>
          <Select
            options={expiryOptions(t)}
            value={expiry}
            onValueChange={(value) => {
              setExpiry(value);
            }}
            className="w-44"
          />
        </Field>
        <Field
          label={t("accounts.tokens.addressesLabel")}
          optional
          description={t("accounts.tokens.addressesHint")}
          error={errors.fields.allowed_cidrs}
        >
          <Textarea
            mono
            rows={2}
            spellCheck={false}
            placeholder="203.0.113.0/24"
            value={cidrs}
            onChange={(event) => {
              setCidrs(event.target.value);
            }}
          />
        </Field>
      </form>
    </Dialog>
  );
}
