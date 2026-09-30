import { Field as BaseField } from "@base-ui/react/field";
import { CircleAlert } from "lucide-react";
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
