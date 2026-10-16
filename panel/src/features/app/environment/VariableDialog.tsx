import { useId, useState } from "react";
import type { SyntheticEvent } from "react";

import { AUDIENCES } from "../../../api/queries/variables";
import type { VariableAudience } from "../../../api/queries/variables";
import { Button } from "../../../components/ui/Button";
import { Dialog } from "../../../components/ui/Dialog";
import { Field } from "../../../components/ui/Field";
import { Input } from "../../../components/ui/Input";
import { Mono } from "../../../components/ui/Mono";
import { Select } from "../../../components/ui/Select";
import { useT } from "../../../i18n";
import { audienceHelp, audienceLabel } from "./audience";
import { nameProblem, valueProblem } from "./dotenv";

/** Adding may start from a name the compose file asks for (`name`). */
export type VariableTarget = { mode: "add"; name?: string } | { mode: "edit"; name: string; value: string; audience: VariableAudience };

export interface VariableDialogProps {
  target: VariableTarget | null;
  /** Names the file has or the draft adds, for the duplicate check when adding. */
  existing: ReadonlySet<string>;
  onClose: () => void;
  onSubmit: (name: string, value: string, audience: VariableAudience) => void;
}

interface FormProps {
  formId: string;
  target: VariableTarget;
  existing: ReadonlySet<string>;
  onSubmit: (name: string, value: string, audience: VariableAudience) => void;
}

/** The references a value can hold, as the server reads them: a syntax, never translated. */
const SCOPES_SYNTAX = "{{team.NAME}}, {{project.NAME}}, {{environment.NAME}}, {{server.NAME}}";
const DEPLOYMENT_SYNTAX = "{{app.URL}}";
const SECRET_SYNTAX = "{{secret:vault/path#key}}";

/** Mounted fresh for every opening, so it starts from the target's values. */
function VariableForm({ formId, target, existing, onSubmit }: FormProps) {
  const t = useT();
  const editing = target.mode === "edit";
  const [name, setName] = useState(target.mode === "edit" ? target.name : (target.name ?? ""));
  const [value, setValue] = useState(editing ? target.value : "");
  const [audience, setAudience] = useState<VariableAudience>(editing ? target.audience : "both");
  const [submitted, setSubmitted] = useState(false);

  const trimmedName = name.trim();
  const duplicate = !editing && existing.has(trimmedName) ? t("environment.variableDialog.duplicate", { name: trimmedName }) : null;
  const nameError = trimmedName === "" && !submitted ? null : (nameProblem(trimmedName, t.locale) ?? duplicate);
  const valueError = valueProblem(value, t.locale);

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    setSubmitted(true);
    if (nameProblem(trimmedName, t.locale) !== null || duplicate !== null || valueError !== null) return;
    onSubmit(trimmedName, value, audience);
  };

  return (
    <form id={formId} onSubmit={submit} noValidate className="flex flex-col gap-4">
      <Field
        label={t("environment.variableDialog.nameLabel")}
        error={nameError}
        {...(editing ? {} : { description: t("environment.variableDialog.nameDescription") })}
      >
        <Input
          mono
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          placeholder="DATABASE_URL"
          value={name}
          disabled={editing}
          onValueChange={(next: string) => {
            setName(next);
          }}
        />
      </Field>
      <Field
        label={t("environment.variableDialog.valueLabel")}
        error={valueError}
        description={
          <>
            {t("environment.variableDialog.valueDescription")}{" "}
            {t.rich("variables.dialog.referenceHelp", {
              scopes: <Mono>{SCOPES_SYNTAX}</Mono>,
              deployment: <Mono>{DEPLOYMENT_SYNTAX}</Mono>,
              secret: <Mono>{SECRET_SYNTAX}</Mono>,
            })}
          </>
        }
      >
        <Input
          mono
          autoComplete="off"
          autoCapitalize="off"
          spellCheck={false}
          value={value}
          onValueChange={(next: string) => {
            setValue(next);
          }}
        />
      </Field>
      <Field label={t("variables.audience.label")} description={`${t("variables.dialog.audienceDescription")} ${audienceHelp(audience, t.locale)}`} nativeLabel={false}>
        <Select
          aria-label={t("variables.audience.label")}
          value={audience}
          options={AUDIENCES.map((option) => ({ value: option, label: audienceLabel(option, t.locale) }))}
          onValueChange={setAudience}
        />
      </Field>
    </form>
  );
}

/**
 * Adds a variable or changes one, as a change saved later with the others. Checked against
 * what the API accepts before it is staged, so a draft can always be saved. A value may refer
 * to a shared variable, to the deployment or to a secret manager; the server resolves those at
 * every deploy.
 */
export function VariableDialog({ target, existing, onClose, onSubmit }: VariableDialogProps) {
  const t = useT();
  const formId = useId();
  // The last target stays on screen while the dialog animates closed; each opening mounts a
  // fresh form, so an abandoned entry is not there the next time.
  const [shown, setShown] = useState<{ target: VariableTarget; generation: number } | null>(null);
  if (target !== null && target !== shown?.target) setShown({ target, generation: (shown?.generation ?? 0) + 1 });
  const current = target ?? shown?.target ?? null;
  const editing = current?.mode === "edit";
  return (
    <Dialog
      open={target !== null}
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      size="sm"
      title={current?.mode === "edit" ? t("environment.variableDialog.editTitle", { name: current.name }) : t("environment.variableDialog.addTitle")}
      description={t("environment.variableDialog.description")}
      footer={
        <>
          <Button onClick={onClose}>{t("environment.cancel")}</Button>
          <Button type="submit" form={formId} variant="primary">
            {editing ? t("environment.variableDialog.updateButton") : t("environment.addVariable")}
          </Button>
        </>
      }
    >
      {current !== null && shown !== null ? (
        <VariableForm key={shown.generation} formId={formId} target={current} existing={existing} onSubmit={onSubmit} />
      ) : null}
    </Dialog>
  );
}
