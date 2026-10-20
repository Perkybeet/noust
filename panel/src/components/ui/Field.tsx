import { Field as BaseField } from "@base-ui/react/field";
import { CircleAlert } from "lucide-react";
import { useId } from "react";
import type { ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";

export interface FieldProps {
  label: ReactNode;
  /** Help that stays visible: format, consequence, default. */
  description?: ReactNode;
  /**
   * The validation message, usually the `fields[name]` of a 422 from the API. Presence marks
   * the control invalid; the text is shown under it and announced with it.
   */
  error?: string | null | undefined;
  /** Marks the field optional. Required is the default and needs no mark. */
  optional?: boolean;
  disabled?: boolean;
  name?: string;
  /**
   * Base UI's label is a <label> for native inputs. Set to false for controls that are not
   * labelable elements (Select, a Switch group): the label becomes a div wired by aria.
   */
  nativeLabel?: boolean;
  /**
   * A button that acts on the control (Inspect, Browse, Generate), set beside the control and
   * aligned with it rather than with the label, so nobody pushes it down with a magic margin.
   */
  action?: ReactNode;
  children: ReactNode;
  className?: string;
}

/** A label, a control and its help and error text, wired together for assistive technology. */
export function Field({
  label,
  description,
  error,
  optional = false,
  disabled = false,
  name,
  nativeLabel = true,
  action,
  children,
  className,
}: FieldProps) {
  const t = useT();
  const invalid = error !== undefined && error !== null && error !== "";
  return (
    <BaseField.Root
      invalid={invalid}
      disabled={disabled}
      {...(name !== undefined ? { name } : {})}
      className={cx("flex min-w-0 flex-col gap-1.5", className)}
    >
      <BaseField.Label
        nativeLabel={nativeLabel}
        {...(nativeLabel ? {} : { render: <div /> })}
        className="flex items-baseline justify-between gap-3 text-13 font-medium text-fg data-disabled:text-fg-muted"
      >
        <span>{label}</span>
        {optional ? <span className="text-12 font-normal text-fg-faint">{t("common.field.optional")}</span> : null}
      </BaseField.Label>
      {action !== undefined ? (
        <div data-field-row="" className="flex min-w-0 items-start gap-2">
          <div className="min-w-0 flex-1">{children}</div>
          <div className="flex shrink-0 items-center">{action}</div>
        </div>
      ) : (
        children
      )}
      {description !== undefined ? (
        <BaseField.Description className="text-12 text-fg-muted">{description}</BaseField.Description>
      ) : null}
      <BaseField.Error match={invalid} className="flex items-start gap-1.5 text-13 text-fail">
        <CircleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
        <span>{error}</span>
      </BaseField.Error>
    </BaseField.Root>
  );
}

/**
 * The message under a group of controls that is not valid: the glyph and the sentence a `Field`
 * draws under its own control, for the groups that are not one control (a `FieldGroup`, a
 * `ChoiceCards`). The caller wires it to the group with `aria-describedby`.
 */
export function GroupError({ id, children }: { id: string; children: ReactNode }) {
  return (
    <p id={id} data-slot="group-error" className="flex items-start gap-1.5 text-13 text-fail">
      <CircleAlert aria-hidden="true" className="mt-0.5 size-3.5 shrink-0" />
      <span>{children}</span>
    </p>
  );
}

export interface FieldGroupProps {
  /** Names the group: the question its controls answer. A group without a name is not a group. */
  legend: ReactNode;
  /** Help that stays visible under the controls: format, consequence, default. */
  description?: ReactNode;
  /** The validation message of the group as a whole ("Choose at least one"), wired to it and announced with it. */
  error?: string | null | undefined;
  /** Marks the group optional. Required is the default and needs no mark. */
  optional?: boolean;
  children: ReactNode;
  className?: string;
}

/**
 * Related controls under one name and one message: a set of checkboxes, a pair of fields that
 * mean something together (a port and its protocol). A `fieldset` with a `legend`, so a screen
 * reader says the group's name on entering it, and the same error a `Field` shows (a glyph and a
 * sentence in the failure colour), tied to the group with `aria-describedby`. One control is a
 * `Field`; one of a few cards that each need a sentence is `ChoiceCards`.
 */
export function FieldGroup({ legend, description, error, optional = false, children, className }: FieldGroupProps) {
  const t = useT();
  const ids = useId();
  const descriptionId = `${ids}-description`;
  const errorId = `${ids}-error`;
  const invalid = error !== undefined && error !== null && error !== "";
  const describedBy = [description !== undefined ? descriptionId : null, invalid ? errorId : null].filter((id) => id !== null).join(" ");
  return (
    <fieldset
      data-invalid={invalid ? "" : undefined}
      {...(describedBy !== "" ? { "aria-describedby": describedBy } : {})}
      className={cx("m-0 flex min-w-0 flex-col gap-1.5 border-0 p-0", className)}
    >
      <legend className="mb-1.5 flex w-full items-baseline justify-between gap-3 p-0 text-13 font-medium text-fg">
        <span>{legend}</span>
        {optional ? <span className="text-12 font-normal text-fg-faint">{t("common.field.optional")}</span> : null}
      </legend>
      <div className="flex min-w-0 flex-col gap-2">{children}</div>
      {description !== undefined ? (
        <p id={descriptionId} className="text-12 text-fg-muted">
          {description}
        </p>
      ) : null}
      {invalid ? <GroupError id={errorId}>{error}</GroupError> : null}
    </fieldset>
  );
}
