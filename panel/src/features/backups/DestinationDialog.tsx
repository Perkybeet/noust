import { useQuery } from "@tanstack/react-query";
import { Info } from "lucide-react";
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
import { Select } from "../../components/ui/Select";
import { Skeleton } from "../../components/ui/Skeleton";
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
 * for the first time hands off to `ShowKeyDialog`, the only chance to write it down.
 */
export function DestinationDialog({ existing, open, onOpenChange }: DestinationDialogProps) {
  const formId = useId();
  const backends = useQuery({ ...backupDestinationBackendsQuery(), enabled: open });
  const [backend, setBackend] = useState(existing?.backend ?? "");
  const [name, setName] = useState(existing?.name ?? "");
  const [values, setValues] = useState<Record<string, string>>(existing?.settings ?? {});
  const [encrypted, setEncrypted] = useState(existing?.encrypted ?? false);
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

  const canSubmit =
    backend !== "" &&
    (existing !== undefined || name.trim() !== "") &&
    hasRequiredValues(fields, values, configuredSecrets);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (!canSubmit || saving) return;
    const payload = fieldsPayload(fields, values);
    const wasEncrypted = existing?.encrypted ?? false;
    const onSuccess = (): void => {
      if (encrypted && !wasEncrypted) {
        setRevealFor(existing?.name ?? name.trim());
        return;
      }
      close(false);
    };
    if (existing === undefined) {
      create.mutate({ name: name.trim(), backend, fields: payload, encrypted }, { onSuccess });
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
        title={existing ? `Edit ${existing.name}` : "Add backup destination"}
        description="Where backups can be copied, on a schedule or by hand, over rclone."
        footer={
          <>
            <Button disabled={saving} onClick={() => close(false)}>
              Cancel
            </Button>
            <Button type="submit" form={formId} variant="primary" loading={saving} disabled={!canSubmit}>
              {existing ? "Save" : "Add destination"}
            </Button>
          </>
        }
      >
        <form id={formId} onSubmit={submit} className="flex flex-col gap-4">
          {existing === undefined ? (
            <div className="grid gap-4 sm:grid-cols-2">
              <Field label="Name" description="Also its rclone remote name: lowercase letters, digits and '-'.">
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
              <Field label="Backend" nativeLabel={false}>
                {backends.isPending ? (
                  <Skeleton className="h-8 w-full rounded-control" />
                ) : (
                  <Select
                    aria-label="Backend"
                    value={backend}
                    onValueChange={(next) => {
                      setBackend(next);
                      setValues({});
                    }}
                    placeholder="Choose a backend"
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
              Backend: <span className="font-medium text-fg">{backendLabel(existing.backend)}</span>. A destination cannot change backend once created.
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
                <div className="flex items-start gap-2 rounded-control border border-border bg-bg-sunken px-3 py-2.5 text-13 text-fg-muted">
                  <Info aria-hidden="true" className="mt-0.5 size-4 shrink-0" />
                  <p>
                    This backend signs in on your own computer, never on the server. Run{" "}
                    <code translate="no" className="mono rounded-[4px] bg-surface px-1 py-0.5 text-fg">{`rclone authorize "${backend}"`}</code>{" "}
                    there, then paste the JSON it prints below.
                  </p>
                </div>
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
                          placeholder="Choose one"
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
                  label="Encrypt backups before upload"
                  description="Wraps everything sent here in an rclone crypt layer, so the destination itself never sees a readable file."
                />
                {encrypted ? (
                  <p className="flex items-start gap-1.5 pl-6 text-13 text-warn">
                    <span>
                      WASM keeps the key, but if this server is ever lost, so is the only other copy - unless you write it down when it
                      is shown next.{" "}
                      <span className="font-medium">Losing it makes every backup on this destination unrecoverable.</span>
                    </span>
                  </p>
                ) : null}
              </div>
            </>
          ) : null}

          {failed ? (
            <ErrorBlock live compact error={error} title={existing ? "The destination was not saved" : "The destination was not created"} />
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
