import { useQuery } from "@tanstack/react-query";
import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { backupDestinationBackendsQuery } from "../../api/queries/backupDestinations";
import type { Destination } from "../../api/queries/backupDestinations";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Checkbox } from "../../components/ui/Checkbox";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Mono } from "../../components/ui/Mono";
import { Notice } from "../../components/ui/Notice";
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
import { useT } from "../../i18n";
import { SecretInput } from "../settings/channelParts";
import { OAUTH_BACKENDS, backendDescription, backendLabel } from "./backendCatalog";
import { fieldsPayload, hasRequiredValues } from "./destinationForm";
import { ShowKeyDialog } from "./ShowKeyDialog";
import { useDestinationActions } from "./useDestinationActions";

export interface DestinationDialogProps {
  /** Present to edit an existing destination; absent to create one. */
  existing?: Destination;
  open: boolean;
  onOpenChange: (open: boolean) => void;
}

/**
 * Adds or edits a backup destination: pick a backend (creating only - it cannot change once
 * chosen), fill its form, and optionally encrypt what is sent there. Saving an encryption key
 * for the first time hands off to `ShowKeyDialog`, the only chance to write it down. Creating
 * one can instead take a key the operator already has - the one a lost server encrypted its
 * backups with - which is the only way those backups are read again.
 */
export function DestinationDialog({ existing, open, onOpenChange }: DestinationDialogProps) {
  const t = useT();
  const formId = useId();
  const backends = useQuery({ ...backupDestinationBackendsQuery(), enabled: open });
  const [backend, setBackend] = useState(existing?.backend ?? "");
  const [name, setName] = useState(existing?.name ?? "");
  const [values, setValues] = useState<Record<string, string>>(existing?.settings ?? {});
  const [encrypted, setEncrypted] = useState(existing?.encrypted ?? false);
  const [useExistingKey, setUseExistingKey] = useState(false);
  const [keyPassword, setKeyPassword] = useState("");
  const [keyPassword2, setKeyPassword2] = useState("");
  const [revealFor, setRevealFor] = useState<string | null>(null);
  const { create, update } = useDestinationActions();

  const saving = create.isPending || update.isPending;
  const failed = create.isError || update.isError;
  const error = create.error ?? update.error;

  const reset = (): void => {
    setBackend(existing?.backend ?? "");
    setName(existing?.name ?? "");
    setValues(existing?.settings ?? {});
    setEncrypted(existing?.encrypted ?? false);
    setUseExistingKey(false);
    setKeyPassword("");
    setKeyPassword2("");
    create.reset();
    update.reset();
  };

  const close = (next: boolean): void => {
    if (!next && saving) return;
    onOpenChange(next);
    if (!next) reset();
  };

  const chosen = backends.data?.backends.find((candidate) => candidate.backend === backend);
  const fields = chosen?.fields ?? [];
  const oauth = OAUTH_BACKENDS.has(backend);
  const configuredSecrets = new Set(existing?.configured_secret_fields ?? []);

  const givesKey = existing === undefined && encrypted && useExistingKey;
  const canSubmit =
    backend !== "" &&
    (existing !== undefined || name.trim() !== "") &&
    hasRequiredValues(fields, values, configuredSecrets) &&
    (!givesKey || (keyPassword.trim() !== "" && keyPassword2.trim() !== ""));

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!canSubmit || saving) return;
    const payload = fieldsPayload(fields, values);
    const wasEncrypted = existing?.encrypted ?? false;
    const onSuccess = (): void => {
      // A key the operator typed in is one they already hold: nothing new to write down.
      if (encrypted && !wasEncrypted && !givesKey) {
        setRevealFor(existing?.name ?? name.trim());
        return;
      }
      close(false);
    };
    if (existing === undefined) {
      create.mutate(
        {
          name: name.trim(),
          backend,
          fields: payload,
          encrypted,
          encryptionKey: givesKey ? { password: keyPassword.trim(), password2: keyPassword2.trim() } : undefined,
        },
        { onSuccess },
      );
    } else {
      update.mutate({ name: existing.name, fields: payload, encrypted }, { onSuccess });
    }
  };

  return (
    <>
      <Dialog
        open={open}
        onOpenChange={close}
        size="lg"
        title={existing ? t("backups.destinationDialog.titleEdit", { name: existing.name }) : t("backups.destinationDialog.titleNew")}
        description={t("backups.destinationDialog.description")}
        footer={
          <>
            <Button disabled={saving} onClick={() => close(false)}>
              {t("backups.common.cancel")}
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={saving} disabled={!canSubmit}>
              {existing ? t("backups.common.save") : t("backups.destinationDialog.add")}
            </Button>
          </>
        }
      >
        <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
          {existing === undefined ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label={t("backups.destinationDialog.nameLabel")} description={t("backups.destinationDialog.nameDescription")}>
                <Input
                  mono
                  value={name}
                  onValueChange={setName}
                  placeholder="offsite-s3"
                  autoComplete="off"
                  autoCapitalize="off"
                  spellCheck={false}
                  disabled={saving}
                />
              </Field>
              <Field label={t("backups.destinationDialog.backendLabel")} nativeLabel={false}>
                {backends.isPending ? (
                  <Skeleton className="h-8 w-full rounded-control" />
                ) : (
                  <Select
                    aria-label={t("backups.destinationDialog.backendLabel")}
                    value={backend}
                    onValueChange={(next) => {
                      setBackend(next);
                      setValues({});
                    }}
                    placeholder={t("backups.destinationDialog.chooseBackend")}
                    disabled={saving}
                    options={(backends.data?.backends ?? []).map((option) => {
                      const description = backendDescription(option.backend);
                      return {
                        value: option.backend,
                        label: backendLabel(option.backend),
                        ...(description !== undefined ? { hint: description } : {}),
                      };
                    })}
                  />
                )}
              </Field>
            </div>
          ) : (
            <p className="text-13 text-fg-muted">
              {t.rich("backups.destinationDialog.backendFixed", {
                backend: <span className="font-medium text-fg">{backendLabel(existing.backend)}</span>,
              })}
            </p>
          )}

          {backend !== "" && backends.isPending ? (
            <div aria-hidden="true" className="grid gap-4 sm:grid-cols-2">
              <Skeleton className="h-8 rounded-control" />
              <Skeleton className="h-8 rounded-control" />
            </div>
          ) : null}
          {backend !== "" && !backends.isPending ? (
            <>
              {oauth ? (
                <Notice>
                  {t.rich("backups.destinationDialog.oauthNote", { command: <Mono>{`rclone authorize "${backend}"`}</Mono> })}
                </Notice>
              ) : null}
              <div className="grid gap-4 sm:grid-cols-2">
                {fields.map((field) => {
                  const choices = field.choices ?? [];
                  return (
                    <Field
                      key={field.key}
                      label={field.label}
                      optional={!field.required}
                      description={field.help !== "" ? field.help : undefined}
                    >
                      {choices.length > 0 ? (
                        <Select
                          aria-label={field.label}
                          value={values[field.key] ?? ""}
                          onValueChange={(next) => setValues((current) => ({ ...current, [field.key]: next }))}
                          placeholder={t("backups.fields.chooseOne")}
                          disabled={saving}
                          options={choices.map((choice) => ({ value: choice, label: choice }))}
                        />
                      ) : field.secret ? (
                        <SecretInput
                          label={field.label}
                          placeholder={field.placeholder}
                          value={values[field.key] ?? ""}
                          configured={configuredSecrets.has(field.key)}
                          disabled={saving}
                          onChange={(next) => setValues((current) => ({ ...current, [field.key]: next }))}
                        />
                      ) : (
                        <Input
                          mono
                          value={values[field.key] ?? ""}
                          onValueChange={(next: string) => setValues((current) => ({ ...current, [field.key]: next }))}
                          placeholder={field.placeholder}
                          autoComplete="off"
                          spellCheck={false}
                          disabled={saving}
                        />
                      )}
                    </Field>
                  );
                })}
              </div>

              <div className="flex flex-col gap-2 rounded-control border border-border bg-bg-sunken px-3 py-2.5">
                <Checkbox
                  checked={encrypted}
                  onCheckedChange={setEncrypted}
                  disabled={saving}
                  label={t("backups.destinationDialog.encrypt.label")}
                  description={t("backups.destinationDialog.encrypt.description")}
                />
                {encrypted && existing === undefined ? (
                  <div className="flex flex-col gap-3 pl-6">
                    <Checkbox
                      checked={useExistingKey}
                      onCheckedChange={setUseExistingKey}
                      disabled={saving}
                      label={t("backups.destinationDialog.haveKey.label")}
                      description={t("backups.destinationDialog.haveKey.description")}
                    />
                    {useExistingKey ? (
                      <div className="grid gap-4 sm:grid-cols-2">
                        <Field label={t("backups.fields.password")}>
                          <SecretInput
                            label={t("backups.fields.password")}
                            placeholder=""
                            value={keyPassword}
                            configured={false}
                            disabled={saving}
                            onChange={setKeyPassword}
                          />
                        </Field>
                        <Field label={t("backups.fields.password2")}>
                          <SecretInput
                            label={t("backups.fields.password2")}
                            placeholder=""
                            value={keyPassword2}
                            configured={false}
                            disabled={saving}
                            onChange={setKeyPassword2}
                          />
                        </Field>
                      </div>
                    ) : null}
                  </div>
                ) : null}
                {encrypted && !useExistingKey ? (
                  <Notice tone="warning" title={t("backups.destinationDialog.encryptWarningBold")} className="ml-6">
                    {t("backups.destinationDialog.encryptWarning")}
                  </Notice>
                ) : null}
              </div>
            </>
          ) : null}

          {failed ? (
            <ErrorBlock
              live
              compact
              error={error}
              title={existing ? t("backups.destinationDialog.errorSave") : t("backups.destinationDialog.errorCreate")}
            />
          ) : null}
        </form>
      </Dialog>
      {revealFor !== null ? (
        <ShowKeyDialog
          name={revealFor}
          open
          onOpenChange={(next) => {
            if (!next) {
              setRevealFor(null);
              close(false);
            }
          }}
        />
      ) : null}
    </>
  );
}
