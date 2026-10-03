import { Menu as BaseMenu } from "@base-ui/react/menu";
import { useId } from "react";
import type { ReactElement, ReactNode } from "react";

import { cx } from "../../lib/cx";
import { Kbd } from "./Kbd";
import { POPUP_MOTION } from "./Tooltip";

export interface MenuProps {
  /** The element that opens the menu, usually a Button or IconButton. */
  trigger: ReactElement<Record<string, unknown>>;
  children: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  align?: "start" | "center" | "end";
  open?: boolean;
  onOpenChange?: (open: boolean) => void;
}

/** A list of actions on one subject, opened from a button. */
export function Menu({ trigger, children, side = "bottom", align = "start", open, onOpenChange }: MenuProps) {
  return (
    <BaseMenu.Root
      {...(open !== undefined ? { open } : {})}
      {...(onOpenChange ? { onOpenChange: (next: boolean) => onOpenChange(next) } : {})}
    >
      <BaseMenu.Trigger render={trigger} />
      <BaseMenu.Portal>
        <BaseMenu.Positioner side={side} align={align} sideOffset={4} className="z-overlay outline-none">
          <BaseMenu.Popup
            className={cx(
              "min-w-48 rounded-card border border-border bg-surface-raised p-1 text-fg shadow-overlay outline-none",
              POPUP_MOTION,
            )}
          >
            {children}
          </BaseMenu.Popup>
        </BaseMenu.Positioner>
      </BaseMenu.Portal>
    </BaseMenu.Root>
  );
}

export interface MenuItemProps {
  children: ReactNode;
  onClick?: () => void;
  icon?: ReactNode;
  /** Keys of the equivalent shortcut. */
  shortcut?: readonly string[];
  /** For actions that destroy something: set in the fail colour. */
  destructive?: boolean;
  disabled?: boolean;
  /**
   * One sentence under the label, read as the item's description: what the action does not
   * do, when that is what the operator would otherwise ask ("Nothing in the application
   * changes"). Most items need none.
   */
  description?: string;
}

export function MenuItem({ children, onClick, icon, shortcut, destructive = false, disabled = false, description }: MenuItemProps) {
  const descriptionId = useId();
  const labelId = useId();
  const described = description !== undefined && description !== "";
  return (
    <BaseMenu.Item
      {...(onClick ? { onClick } : {})}
      // Named by its label alone: the description is read after it, not as part of the name.
      {...(described ? { "aria-labelledby": labelId, "aria-describedby": descriptionId } : {})}
      disabled={disabled}
      className={cx(
        "flex cursor-pointer gap-2.5 rounded-control px-2 text-13 outline-none select-none",
        described ? "max-w-80 items-start py-1.5" : "h-8 items-center",
        "data-disabled:cursor-not-allowed data-disabled:opacity-50",
        destructive
          ? "text-fail data-highlighted:bg-fail-soft"
          : "text-fg data-highlighted:bg-surface-hover",
        "[&_svg]:size-4 [&_svg]:shrink-0",
      )}
    >
      {icon !== undefined ? (
        <span aria-hidden="true" className={cx("flex", destructive ? "text-fail" : "text-fg-muted")}>
          {icon}
        </span>
      ) : null}
      {described ? (
        <span className="flex min-w-0 flex-1 flex-col">
          <span id={labelId} className="truncate">
            {children}
          </span>
          <span id={descriptionId} className="text-12 text-pretty whitespace-normal text-fg-muted">
            {description}
          </span>
        </span>
      ) : (
        <span className="flex-1 truncate">{children}</span>
      )}
      {shortcut && shortcut.length > 0 ? (
        <span className="ml-4 flex gap-0.5" aria-hidden="true">
          {shortcut.map((key) => (
            <Kbd key={key}>{key}</Kbd>
          ))}
        </span>
      ) : null}
    </BaseMenu.Item>
  );
}

export function MenuSeparator() {
  return <BaseMenu.Separator className="-mx-1 my-1 h-px bg-border" />;
}

export function MenuGroup({ label, children }: { label: string; children: ReactNode }) {
  return (
    <BaseMenu.Group>
      <BaseMenu.GroupLabel className="px-2 pt-1.5 pb-1 text-12 font-medium text-fg-faint select-none">
        {label}
      </BaseMenu.GroupLabel>
      {children}
    </BaseMenu.Group>
  );
}
