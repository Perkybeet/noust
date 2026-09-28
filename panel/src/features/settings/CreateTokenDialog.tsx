import { useMutation, useQueryClient } from "@tanstack/react-query";
import { TriangleAlert } from "lucide-react";
import { useId, useRef, useState } from "react";
import type { SyntheticEvent } from "react";

import { authKeys, createApiToken } from "../../api/queries/auth";
import type { CreatedToken } from "../../api/queries/auth";
import { CommandHint } from "../../components/page/CommandHint";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { CopyTextButton } from "../../components/ui/CopyTextButton";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { toast } from "../../components/ui/toast";
import { useT } from "../../i18n";
import type { T } from "../../i18n";
import { cx } from "../../lib/cx";
import { splitErrors } from "./formErrors";
import { DEFAULT_EXPIRY, expiryOptions, expiryPhrase, scopes } from "./tokens";
import type { TokenScope } from "./tokens";

export interface CreateTokenDialogProps {
  open: boolean;
  onClose: () => void;
}

const FIELDS = ["name", "scope", "expires_hours"] as const;

/** The scope choice: one card per scope, each saying what a token of that scope can do. */
function ScopePicker({ t, value, onChange, error }: { t: T; value: TokenScope; onChange: (scope: TokenScope) => void; error?: string | undefined }) {
  const name = useId();
  return (
    <fieldset className="flex flex-col gap-2">
      <legend className="mb-1.5 text-13 font-medium text-fg">{t("settings.tokens.create.scopeLegend")}</legend>
      {scopes(t).map((scope) => (
        <label
          key={scope.value}
          className={cx(
            "grid cursor-pointer grid-cols-[auto_minmax(0,1fr)] items-start gap-x-3 gap-y-0.5 rounded-control border px-3 py-2.5",
            "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-1 has-[:focus-visible]:outline-focus",
            value === scope.value ? "border-accent bg-accent-soft" : "border-border hover:bg-surface-hover",
          )}
        >
          <input
            type="radio"
            name={name}
            value={scope.value}
            checked={value === scope.value}
            onChange={() => {
              onChange(scope.value);
            }}
            aria-labelledby={`${name}-${scope.value}-label`}
            aria-describedby={`${name}-${scope.value}`}
            className="row-span-2 mt-0.5 size-4 shrink-0 accent-accent"
          />
          <span id={`${name}-${scope.value}-label`} className="text-14 font-medium text-fg">
            {scope.label}
          </span>
          <span id={`${name}-${scope.value}`} className="col-start-2 text-13 text-fg-muted">
            {scope.description}
          </span>
        </label>
      ))}
      {error !== undefined ? <p className="text-13 text-fail">{error}</p> : null}
    </fieldset>
  );
}

/** The token, once: copy it now, because only its hash is kept. */
function TokenOnce({ t, token }: { t: T; token: CreatedToken }) {
  const labelId = useId();
  const example = `curl -H "Authorization: Bearer ${token.token}" ${window.location.origin}/api/apps`;
  return (
    <div className="flex flex-col gap-4">
      <div role="alert" className="flex items-start gap-2.5 rounded-control border border-warn/40 bg-warn-soft px-3 py-2.5">
        <TriangleAlert aria-hidden="true" className="mt-0.5 size-4 shrink-0 text-warn" />
        <p className="text-13 text-fg">{t("settings.tokens.once.warning")}</p>
      </div>
      <div className="flex flex-col gap-1.5">
        <span id={labelId} className="text-13 font-medium text-fg">{t("settings.tokens.once.tokenLabel", { name: token.name })}</span>
        {/* All of it, wrapped: a token cut off by a narrow field cannot be checked by eye. */}
        <code
          aria-labelledby={labelId}
          translate="no"
          data-testid="new-token"
          className="rounded-control border border-border-strong bg-surface px-3 py-2 text-13 break-all text-fg select-all"
        >
          {token.token}
        </code>
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
  const [created, setCreated] = useState<CreatedToken | null>(null);
  const nameRef = useRef<HTMLInputElement>(null);
  const formId = useId();

  const create = useMutation({
    mutationFn: () =>
      createApiToken({
        name: name.trim(),
        scope,
        expires_hours: expiryOptions(t).find((option) => option.value === expiry)?.hours ?? null,
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
        <ScopePicker t={t} value={scope} onChange={setScope} error={errors.fields.scope} />
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
      </form>
    </Dialog>
  );
}
