import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { AUDIENCES } from "../../api/queries/variables";
import type { VariableAudience } from "../../api/queries/variables";
import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { Select } from "../../components/ui/Select";
import { useT } from "../../i18n";
import { audienceHelp, audienceLabel } from "../app/environment/audience";
import { nameProblem, valueProblem } from "../app/environment/dotenv";

/** Whether Noust decides a variable is a secret, or the operator says. */
export type SecretChoice = "auto" | "secret" | "plain";

export interface SharedVariableDraft {
  name: string;
  value: string;
  /** true or false to say what it is; null to let Noust decide. */
  secret: boolean | null;
  audience: VariableAudience;
}

export type SharedVariableTarget =
  | { mode: "add" }
  | { mode: "edit"; name: string; value: string; secret: boolean | null; audience: VariableAudience };

export interface SharedVariableDialogProps {
  target: SharedVariableTarget | null;
  /** The scope's names, for the duplicate check when adding. */
  existing: ReadonlySet<string>;
  onClose: () => void;
  /** Saves it; the dialog stays open, busy, until it settles, and shows a refusal as given. */
  onSubmit: (draft: SharedVariableDraft) => Promise<void>;
}

function choiceOf(secret: boolean | null): SecretChoice {
  return secret === null ? "auto" : secret ? "secret" : "plain";
}

function secretOf(choice: SecretChoice): boolean | null {
  return choice === "auto" ? null : choice === "secret";
}

interface FormProps {
  formId: string;
  target: SharedVariableTarget;
  existing: ReadonlySet<string>;
  onSubmit: (draft: SharedVariableDraft) => Promise<void>;
  onPending: (pending: boolean) => void;
}

/** Mounted fresh for every opening, so it starts from the target's values. */
function SharedVariableForm({ formId, target, existing, onSubmit, onPending }: FormProps) {
  const t = useT();
  const editing = target.mode === "edit";
  const [name, setName] = useState(editing ? target.name : "");
  const [value, setValue] = useState(editing ? target.value : "");
  const [secret, setSecret] = useState<SecretChoice>(editing ? choiceOf(target.secret) : "auto");
  const [audience, setAudience] = useState<VariableAudience>(editing ? target.audience : "both");
  const [submitted, setSubmitted] = useState(false);
  const [failure, setFailure] = useState<unknown>(null);

  const trimmed = name.trim();
  const duplicate = !editing && existing.has(trimmed) ? t("environment.variableDialog.duplicate", { name: trimmed }) : null;
  const nameError = trimmed === "" && !submitted ? null : (nameProblem(trimmed, t.locale) ?? duplicate);
  const valueError = valueProblem(value, t.locale);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (nameProblem(trimmed, t.locale) !== null || duplicate !== null || valueError !== null) return;
    setFailure(null);
    onPending(true);
    onSubmit({ name: trimmed, value, secret: secretOf(secret), audience }).then(
      () => onPending(false),
      (error: unknown) => {
        onPending(false);
        setFailure(error);
      },
    );
  };

  return (
    <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
      <Field label={t("variables.shared.dialog.nameLabel")} error={nameError} {...(editing ? {} : { description: t("variables.shared.dialog.nameDescription") })}>
        <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} placeholder="STRIPE_PUBLIC_KEY" value={name} disabled={editing} onValueChange={setName} />
      </Field>
      <Field label={t("variables.shared.dialog.valueLabel")} error={valueError} description={t("variables.shared.dialog.valueDescription")}>
        <Input mono autoComplete="off" autoCapitalize="off" spellCheck={false} value={value} onValueChange={setValue} />
      </Field>
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label={t("variables.shared.dialog.secretLabel")} description={t("variables.shared.dialog.secretDescription")} nativeLabel={false}>
          <Select<SecretChoice>
            aria-label={t("variables.shared.dialog.secretLabel")}
            value={secret}
            options={[
              { value: "auto", label: t("variables.shared.dialog.secretAuto") },
              { value: "secret", label: t("variables.shared.dialog.secretYes") },
              { value: "plain", label: t("variables.shared.dialog.secretNo") },
            ]}
            onValueChange={setSecret}
          />
        </Field>
        <Field label={t("variables.audience.label")} description={audienceHelp(audience, t.locale)} nativeLabel={false}>
          <Select
            aria-label={t("variables.audience.label")}
            value={audience}
            options={AUDIENCES.map((option) => ({ value: option, label: audienceLabel(option, t.locale) }))}
            onValueChange={setAudience}
          />
        </Field>
      </div>
      {failure !== null ? <ErrorBlock live compact error={failure} title={t("variables.shared.saveFailed", { name: trimmed })} /> : null}
    </form>
  );
}

/**
 * Adds a shared variable or changes one. Unlike an application's variables it is saved at
 * once, one at a time: the scope has no file to rewrite, and the applications that refer to it
 * read it at their next deploy. A refusal is shown beside the value, as the server worded it.
 */
export function SharedVariableDialog({ target, existing, onClose, onSubmit }: SharedVariableDialogProps) {
  const t = useT();
  const formId = useId();
  const [pending, setPending] = useState(false);
  // The last target stays on screen while the dialog animates closed; each opening mounts a fresh form.
  const [shown, setShown] = useState<{ target: SharedVariableTarget; generation: number } | null>(null);
  if (target !== null && target !== shown?.target) setShown({ target, generation: (shown?.generation ?? 0) + 1 });
  const current = target ?? shown?.target ?? null;
  const editing = current?.mode === "edit";
  return (
    <Dialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open && !pending) onClose();
      }}
      size="md"
      title={current?.mode === "edit" ? t("variables.shared.dialog.editTitle", { name: current.name }) : t("variables.shared.dialog.addTitle")}
      description={t("variables.shared.dialog.description")}
      footer={
        <>
          <Button disabled={pending} onClick={onClose}>
            {t("variables.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={pending}>
            {editing ? t("variables.shared.dialog.submitEdit") : t("variables.shared.dialog.submitAdd")}
          </Button>
        </>
      }
    >
      {current !== null && shown !== null ? (
        <SharedVariableForm key={shown.generation} formId={formId} target={current} existing={existing} onSubmit={onSubmit} onPending={setPending} />
      ) : null}
    </Dialog>
  );
}
