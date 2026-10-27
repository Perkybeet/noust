import { Button as BaseButton } from "@base-ui/react/button";
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";
import { useDemoted } from "./demotePrimary";
import { Spinner } from "./Spinner";

export type ButtonVariant = "primary" | "secondary" | "ghost" | "danger";
export type ButtonSize = "sm" | "md" | "lg";

// What is pressed is raised (docs/DESIGN.md 2.6): it rises 1px under the pointer and sinks 1px
// when pressed, and the primary and the danger buttons carry their own edge. A ghost button is
// text on the page and stays flat.
const BASE =
  "relative inline-flex shrink-0 cursor-pointer items-center justify-center whitespace-nowrap rounded-control font-semibold select-none " +
  "focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-focus " +
  "data-disabled:cursor-not-allowed data-disabled:not-aria-busy:opacity-50 aria-busy:cursor-progress";

const VARIANTS: Record<ButtonVariant, string> = {
  primary: "elev-raised-accent bg-accent text-on-accent hover:not-data-disabled:bg-accent-hover",
  secondary: "elev-raised bg-surface-raised text-fg",
  ghost:
    "bg-transparent font-medium text-fg-muted transition-colors duration-(--duration-press) ease-out " +
    "hover:not-data-disabled:bg-bg-sunken hover:not-data-disabled:text-fg active:not-data-disabled:bg-bg-sunken-deep",
  danger: "elev-raised-fail bg-fail-strong text-on-fail",
};

const SIZES: Record<ButtonSize, string> = {
  sm: "h-control-sm gap-1.5 px-3 text-13 [&_svg]:size-icon-sm",
  md: "h-control-md gap-2 px-3.5 text-13 [&_svg]:size-icon-md",
  lg: "h-control-lg gap-2 px-5 text-14 [&_svg]:size-icon-md",
};

/** The class string of a button, for elements that must look like one (links, triggers). */
export function buttonClassName(
  variant: ButtonVariant = "secondary",
  size: ButtonSize = "md",
  className?: string,
): string {
  return cx(BASE, VARIANTS[variant], SIZES[size], className);
}

export interface ButtonProps extends Omit<BaseButton.Props, "className" | "children"> {
  variant?: ButtonVariant;
  size?: ButtonSize;
  /** Shows a spinner, keeps the label and focus, and blocks further presses. */
  loading?: boolean;
  /** Leading icon; replaced by the spinner while loading. */
  icon?: ReactNode;
  trailingIcon?: ReactNode;
  className?: string;
  children?: ReactNode;
}

/**
 * The one button. Label with a verb that says what happens ("Deploy", "Restart service"),
 * and keep that verb through the flow: the toast after "Deploy" says "Deployed".
 */
export function Button({
  variant = "secondary",
  size = "md",
  loading = false,
  disabled = false,
  icon,
  trailingIcon,
  className,
  children,
  type = "button",
  ...rest
}: ButtonProps) {
  // While the save bar holds changes the header's primary steps aside (PageHeader, FM-2): the
  // colour changes, the label and the size do not.
  const effective = useDemoted() && variant === "primary" ? "secondary" : variant;
  return (
    <BaseButton
      {...rest}
      type={type}
      disabled={disabled || loading}
      focusableWhenDisabled={loading}
      aria-busy={loading || undefined}
      // Read by the design contract (one primary per view) and by the gallery.
      data-variant={effective}
      className={buttonClassName(effective, size, className)}
    >
      {loading ? <Spinner size={size === "sm" ? 14 : 16} /> : icon}
      {children}
      {trailingIcon}
    </BaseButton>
  );
}
