import { useId, useRef, useState } from "react";
import type { ReactNode, SyntheticEvent } from "react";

import { ErrorBlock } from "../../components/page/QueryState";
import { Button } from "../../components/ui/Button";
import { Dialog } from "../../components/ui/Dialog";
import { Field } from "../../components/ui/Field";
import { Input } from "../../components/ui/Input";
import { useT } from "../../i18n";
import { splitErrors } from "../settings/formErrors";

export interface NameValue {
  name: string;
  /** The short name the operator typed, or "" to let the server make it from the name. */
  slug: string;
}

export interface NameDialogProps {
  open: boolean;
  onOpenChange: (open: boolean) => void;
  title: string;
  description?: ReactNode;
  nameLabel: string;
  nameHelp?: string;
  /** The verb and its object on the one primary button: "Create project", "Save name". */
  submitLabel: string;
  initialName?: string;
  /** Offers the short name too, which only a new thing has: commands and links name it by that. */
  slug?: { label: string; help: string };
  /** Fields of the form that are not a name: the team a project goes to. */
  children?: ReactNode;
  /** Nothing to save: the same name, or an empty one. Renames pass the current name. */
  unchanged?: (value: NameValue) => boolean;
  /** Runs it. A refusal is shown in the dialog, as the server worded it, and the dialog stays open. */
  onSubmit: (value: NameValue) => Promise<void>;
}

/**
 * One small form for everything a team, a project or an environment is called: creating it
 * (a name, and optionally the short name commands use) and renaming it. The server's refusals
 * ("a project of that name exists") are shown beside the field they are about, verbatim.
 */
export function NameDialog({ open, onOpenChange, title, description, nameLabel, nameHelp, submitLabel, initialName = "", slug, children, unchanged, onSubmit }: NameDialogProps) {
  const t = useT();
  const formId = useId();
  const nameRef = useRef<HTMLInputElement>(null);
  const [name, setName] = useState(initialName);
  const [short, setShort] = useState("");
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<unknown>(null);
  const errors = splitErrors(error, ["name", "slug"] as const);

  // Each opening starts from what the dialog is given, not from what the last one left typed.
  const [wasOpen, setWasOpen] = useState(open);
  if (open !== wasOpen) {
    setWasOpen(open);
    if (open) {
      setName(initialName);
      setShort("");
      setError(null);
      setPending(false);
    }
  }

  const close = (next: boolean): void => {
    if (pending) return;
    onOpenChange(next);
  };

  const submit = (event: SyntheticEvent<HTMLFormElement>): void => {
    event.preventDefault();
    if (pending) return;
    const value = { name: name.trim(), slug: short.trim() };
    if (value.name === "") {
      setError({ fields: { name: t("teams.name.required") } });
      nameRef.current?.focus();
      return;
    }
    if (unchanged?.(value) === true) {
      onOpenChange(false);
      return;
    }
    setPending(true);
    setError(null);
    onSubmit(value).then(
      () => {
        setPending(false);
        onOpenChange(false);
      },
      (failure: unknown) => {
        setPending(false);
        setError(failure);
      },
    );
  };

  return (
    <Dialog
      open={open}
      onOpenChange={close}
      size="md"
      initialFocus={nameRef}
      title={title}
      description={description}
      footer={
        <>
          <Button disabled={pending} onClick={() => close(false)}>
            {t("common.words.cancel")}
          </Button>
          <Button type="submit" form={formId} variant="primary" loading={pending}>
            {submitLabel}
          </Button>
        </>
      }
    >
      <form id={formId} noValidate onSubmit={submit} className="flex flex-col gap-5">
        {errors.form !== null ? <ErrorBlock live compact error={errors.form} title={t("teams.name.failed")} /> : null}
        <Field label={nameLabel} {...(nameHelp !== undefined ? { description: nameHelp } : {})} error={errors.fields.name}>
          <Input
            ref={nameRef}
            autoComplete="off"
            maxLength={128}
            value={name}
            onValueChange={(value: string) => {
              setName(value);
            }}
          />
        </Field>
        {slug !== undefined ? (
          <Field label={slug.label} description={slug.help} optional error={errors.fields.slug}>
            <Input
              mono
              autoComplete="off"
              autoCapitalize="off"
              spellCheck={false}
              maxLength={64}
              value={short}
              onValueChange={(value: string) => {
                setShort(value);
              }}
            />
          </Field>
        ) : null}
        {children}
      </form>
    </Dialog>
  );
}
