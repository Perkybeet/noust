import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { authKeys, createApiToken, sessionQuery } from "../../api/queries/auth";
import type { CreatedToken } from "../../api/queries/auth";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { CopyTextButton } from "../../components/ui/CopyTextButton";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { InlineMessage } from "../../components/ui/InlineMessage";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Textarea } from "../../components/ui/Textarea";
import { toast } from "../../components/ui/toast";
import { hasMessage, useT } from "../../i18n";
import type { PlainKey, T } from "../../i18n";
import { ChoiceCards } from "../../components/ui/ChoiceCards";
import type { ChoiceCard } from "../../components/ui/ChoiceCards";
import { rolesQuery } from "./accounts/api";
import type { RolesResponse } from "./accounts/api";
import { focusFirstInvalid, splitErrors } from "./formErrors";
import { NO_REACH, reachBody, reachReady } from "./tokenReach";
import type { TokenReach } from "./tokenReach";
import { TokenReachFields } from "./TokenReachFields";
import { DEFAULT_EXPIRY, expiryOptions, expiryPhrase, heldPermissions, scopes } from "./tokens";
import type { TokenScope } from "./tokens";

/** What the first card of the choice of level is: not a scope but a name for what one job needs (item 163). */
const LIMITED = "limited" as const;
type Level = TokenScope | typeof LIMITED;

/** The preset that opens the choice: what a pipeline most often needs. */
const DEFAULT_PRESET = "deploy_only";

export interface CreateTokenDialogProps {
  open: boolean;
  onClose: () => void;
}

const FIELDS = ["name", "scope", "expires_hours", "allowed_cidrs", "team", "project", "environment"] as const;

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

/** What a token is asked to hold: the preset's permissions, or the scope's, as the server says them; undefined until it does. */
function wantedPermissions(roles: RolesResponse | undefined, level: Level, preset: string | null): string[] | undefined {
  if (roles === undefined) return undefined;
  if (preset !== null) return [...(roles.token_presets?.find((item) => item.id === preset)?.permissions ?? [])];
  return level === LIMITED ? [] : scopePermissions(roles.roles, level);
}

/** The addresses a token is limited to, one per line or separated by commas. */
export function parseCidrs(text: string): string[] {
  return text
    .split(/[\s,]+/)
    .map((part) => part.trim())
    .filter((part) => part !== "");
}

/**
 * What the token will be able to do: what it is asked to (its scope, or the preset chosen), capped by what its owner may
 * do today, and, tied to a team, a project or an environment, by what a team can own. A token never does more than the
 * person it belongs to, and one tied to a team never reaches the server.
 */
function Capped({ t, wanted, own, owner, tenant }: { t: T; wanted: readonly string[] | undefined; own: readonly string[]; owner: string | null; tenant: readonly string[] | null }) {
  if (wanted === undefined) return null;
  const { kept, droppedByOwner, droppedByReach } = heldPermissions(wanted, own, tenant);
  return (
    <div className="flex flex-col gap-1.5 text-13">
      <p className="font-medium text-fg">{owner === null ? t("accounts.tokens.cappedMaster") : t("accounts.tokens.capped", { name: owner })}</p>
      <p className="flex flex-wrap gap-x-2 gap-y-1">
        {kept.map((permission) => (
          <Mono key={permission} tone="default">
            {permission}
          </Mono>
        ))}
      </p>
      {droppedByOwner.length > 0 ? <p className="text-fg-muted">{t("accounts.tokens.dropped", { permissions: droppedByOwner.join(", ") })}</p> : null}
      {droppedByReach.length > 0 ? <p className="text-fg-muted">{t("accounts.tokens.droppedBound", { permissions: droppedByReach.join(", ") })}</p> : null}
    </div>
  );
}

/** A preset in the console's words; one this console has not heard of (a newer server's) is its name. */
function presetWords(t: T, id: string): { label: string; hint: string | undefined } {
  const label = `settings.tokens.presets.${id}.label`;
  const hint = `settings.tokens.presets.${id}.hint`;
  return {
    label: hasMessage(label) ? t(label as PlainKey) : id,
    hint: hasMessage(hint) ? t(hint as PlainKey) : undefined,
  };
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
      <CommandHint label={t("settings.tokens.once.tryIt")} command={example} reason="otro-servidor" />
    </div>
  );
}

/** Issuing an API token: a name, a scope and an expiry, then the token itself, once. */
export function CreateTokenDialog({ open, onClose }: CreateTokenDialogProps) {
  const t = useT();
  const queryClient = useQueryClient();
  const [name, setName] = useState("");
  const [level, setLevel] = useState<Level>("read");
  const [preset, setPreset] = useState(DEFAULT_PRESET);
  const [expiry, setExpiry] = useState(DEFAULT_EXPIRY);
  const [cidrs, setCidrs] = useState("");
  const [reach, setReach] = useState<TokenReach>(NO_REACH);
  const [created, setCreated] = useState<CreatedToken | null>(null);
  const [tried, setTried] = useState(false);
  const { data: session } = useQuery(sessionQuery());
  const roles = useQuery({ ...rolesQuery(), enabled: open });
  const nameRef = useRef<HTMLInputElement>(null);
  const failureRef = useRef<HTMLDivElement>(null);
  const formId = useId();
  // Signed in with the access token: the token made here belongs to nobody, which is said before anything is filled in.
  const ownerless = session !== undefined && (session.account ?? null) === null;
  const presets = roles.data?.token_presets ?? [];
  // The preset the form holds: the one chosen, or the first the server serves when this server does not have it.
  const chosenPreset = presets.some((item) => item.id === preset) ? preset : (presets[0]?.id ?? preset);
  const limited = level === LIMITED && presets.length > 0;
  const scope: TokenScope = level === LIMITED ? "read" : level;

  const create = useMutation({
    mutationFn: () =>
      createApiToken({
        name: name.trim(),
        scope,
        ...(limited ? { preset: chosenPreset } : {}),
        expires_hours: expiryOptions(t).find((option) => option.value === expiry)?.hours ?? null,
        allow_elevated: false,
        ...(parseCidrs(cidrs).length > 0 ? { allowed_cidrs: parseCidrs(cidrs) } : {}),
        ...reachBody(reach),
      }),
    onSuccess: (token) => {
      setCreated(token);
      void queryClient.invalidateQueries({ queryKey: authKeys.tokens });
    },
  });
  const errors = splitErrors(create.error, FIELDS);
  const failed = errors.form !== null;
  // The refusal is what the person is waiting for: it goes where they are looking (the form can be longer than the window)
  // and the keyboard starts from it, so it is neither above the fold nor behind the cards.
  useEffect(() => {
    if (!failed) return;
    failureRef.current?.scrollIntoView({ block: "nearest" });
    failureRef.current?.focus();
  }, [failed, create.error]);

  const reset = (): void => {
    setName("");
    setLevel("read");
    setPreset(DEFAULT_PRESET);
    setExpiry(DEFAULT_EXPIRY);
    setCidrs("");
    setReach(NO_REACH);
    setCreated(null);
    setTried(false);
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
    // The button is never off for a missing value: it says what is missing, where, and goes there.
    if (name.trim() === "" || !reachReady(reach)) {
      setTried(true);
      focusFirstInvalid(formId);
      return;
    }
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
          <Button type="submit" form={formId} variant="primary" loading={create.isPending}>
            {t("settings.tokens.createToken")}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? (
          <div ref={failureRef} tabIndex={-1} className="rounded-card outline-none">
            <ErrorBlock live compact error={errors.form} title={t("settings.tokens.create.errorTitle")} />
          </div>
        ) : null}
        {ownerless ? (
          <Notice tone="info" title={t("settings.tokens.create.masterTitle")}>
            {session.security_profile === "ens-medium" ? t("settings.tokens.create.masterEns") : t("settings.tokens.create.masterBody")}
          </Notice>
        ) : null}
        <Field label={t("settings.tokens.create.nameLabel")} description={t("settings.tokens.create.nameDescription")} error={errors.fields.name ?? (tried && name.trim() === "" ? t("settings.shared.required") : undefined)}>
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
        <ChoiceCards<Level>
          legend={t("settings.tokens.create.scopeLegend")}
          options={[
            ...scopes(t).map((option): ChoiceCard<Level> => ({ value: option.value, label: option.label, description: option.description })),
            // Offered where the server serves the names: a server that does not would make a plain read token of it.
            ...(presets.length > 0 ? [{ value: LIMITED, label: t("settings.tokens.scopes.limited.label"), description: t("settings.tokens.scopes.limited.description") } satisfies ChoiceCard<Level>] : []),
          ]}
          value={limited ? LIMITED : level}
          onValueChange={setLevel}
        />
        {errors.fields.scope !== undefined ? <p className="text-13 text-fail">{errors.fields.scope}</p> : null}
        {limited ? (
          <Field label={t("settings.tokens.presets.field")} nativeLabel={false}>
            <Select
              options={presets.map((item) => {
                const words = presetWords(t, item.id);
                return { value: item.id, label: words.label, ...(words.hint !== undefined ? { hint: words.hint } : {}) };
              })}
              value={chosenPreset}
              onValueChange={setPreset}
            />
          </Field>
        ) : null}
        <TokenReachFields reach={reach} onChange={setReach} errors={errors.fields} needsAccount={ownerless} />
        {tried && !reachReady(reach) ? <InlineMessage tone="error" live>{t("settings.tokens.create.reachRequired")}</InlineMessage> : null}
        <Capped
          t={t}
          wanted={wantedPermissions(roles.data, level, limited ? chosenPreset : null)}
          own={session?.permissions ?? []}
          owner={session?.account?.username ?? null}
          tenant={reach.kind === "all" ? null : (roles.data?.tenant_permissions ?? null)}
        />
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
