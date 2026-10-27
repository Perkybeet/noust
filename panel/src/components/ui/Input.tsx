import { Input as BaseInput } from "@base-ui/react/input";
import type { ReactNode } from "react";

import { cx } from "../../lib/cx";

/**
 * The frame shared by every text control. A field is carved into the page (docs/DESIGN.md 2.6): a
 * darker stone with the shadow on top and the light below, deeper under the pointer, and a focus
 * ring around it; an invalid one keeps a ring of the failure colour, which the field's error
 * message under it explains.
 */
export const CONTROL_FRAME =
  "elev-carved rounded-control bg-bg-sunken text-fg " +
  "transition-colors duration-(--duration-press) ease-out hover:bg-bg-sunken-deep " +
  "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-focus " +
  "has-[[aria-invalid=true]]:outline-2 has-[[aria-invalid=true]]:outline-fail has-[[data-invalid]]:outline-2 has-[[data-invalid]]:outline-fail " +
  "has-[:disabled]:cursor-not-allowed has-[:disabled]:opacity-60 has-[:disabled]:hover:bg-bg-sunken";

export interface InputProps extends Omit<BaseInput.Props, "className" | "size" | "prefix"> {
  size?: "sm" | "md";
  /** System values (paths, ports, hostnames) are typed in mono. */
  mono?: boolean;
  /** Leading icon, decorative. */
  icon?: ReactNode;
  /** Fixed text before the value, such as `https://`. */
  prefix?: ReactNode;
  /** Fixed text or a control after the value, such as a unit or a copy button. */
  suffix?: ReactNode;
  className?: string;
}

/** A single-line text control. Wrap it in Field for a label, help and error. */
export function Input({ size = "md", mono = false, icon, prefix, suffix, className, ...rest }: InputProps) {
  return (
    <div
      className={cx(
        "flex min-w-0 items-center",
        CONTROL_FRAME,
        size === "sm" ? "h-control-sm text-13" : "h-control-md text-13",
        className,
      )}
    >
      {icon !== undefined ? (
        <span aria-hidden="true" className="flex pl-2.5 text-fg-faint [&_svg]:size-4">
          {icon}
        </span>
      ) : null}
      {prefix !== undefined ? (
        <span className="mono flex h-full items-center border-r border-border pr-2 pl-2.5 text-fg-faint select-none">
          {prefix}
        </span>
      ) : null}
      <BaseInput
        {...rest}
        className={cx(
          "h-full w-full min-w-0 flex-1 bg-transparent px-2.5 outline-none placeholder:text-fg-faint disabled:cursor-not-allowed",
          icon !== undefined && "pl-2",
          mono && "mono",
        )}
      />
      {suffix !== undefined ? (
        <span className="flex h-full shrink-0 items-center gap-1 pr-1 pl-1 text-fg-faint">{suffix}</span>
      ) : null}
    </div>
  );
}
