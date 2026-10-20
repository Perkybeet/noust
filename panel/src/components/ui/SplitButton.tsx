import { ChevronDown } from "lucide-react";
import type { MouseEvent, ReactNode } from "react";

import { useT } from "../../i18n";
import { cx } from "../../lib/cx";
import { Button } from "./Button";
import { useDemoted } from "./demotePrimary";
import { Menu } from "./Menu";
import type { ButtonSize } from "./Button";

export interface SplitButtonProps {
  /** The main action's label: a verb, "Update". Pressing it does the action. */
  children: ReactNode;
  /**
   * The main action's name as text, for the menu button's accessible name ("More options for
   * Update") and the group's. The same words as the label.
   */
  name: string;
  /** What the main part does. The menu's items do the variations: "Update without cache". */
  onClick?: (event: MouseEvent<HTMLButtonElement>) => void;
  /** `MenuItem`s, seven at most: the same action with another meaning, never a different subject. */
  menu: ReactNode;
  variant?: "primary" | "secondary";
  size?: ButtonSize;
  icon?: ReactNode;
  /** The main action is running: its arc is drawn in the main part and neither part can be pressed. */
  loading?: boolean;
  disabled?: boolean;
  align?: "start" | "center" | "end";
  className?: string;
}

const MENU_WIDTH: Record<ButtonSize, string> = {
  sm: "w-control-sm",
  md: "w-control-md",
  lg: "w-control-lg",
};

/**
 * One action with its variations: the main part does the usual thing, the arrow beside it opens a
 * menu of the same action done another way ("Update" and, in the menu, "Update without cache" and
 * "Build only"). The two parts are one control of one height; each is a button with its own name
 * and one tab stop, and the focus ring goes round the whole control, so it is never cut where they
 * meet. A menu of different subjects is a `Menu` on its own button, not this.
 */
export function SplitButton({ children, name, onClick, menu, variant = "secondary", size = "md", icon, loading = false, disabled = false, align = "end", className }: SplitButtonProps) {
  const t = useT();
  // While the save bar holds changes a primary is drawn as a secondary: the seam follows the colour.
  const effective = useDemoted() && variant === "primary" ? "secondary" : variant;
  const primary = effective === "primary";
  return (
    <div
      role="group"
      aria-label={name}
      data-slot="split-button"
      className={cx(
        "inline-flex shrink-0 rounded-control",
        // One ring for the pair: the parts draw none of their own (the selector outweighs theirs).
        "has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-focus [&_button:focus-visible]:outline-none",
        className,
      )}
    >
      <Button
        variant={variant}
        size={size}
        {...(icon !== undefined ? { icon } : {})}
        loading={loading}
        disabled={disabled}
        {...(onClick ? { onClick } : {})}
        className="rounded-r-none"
      >
        {children}
      </Button>
      <Menu
        align={align}
        trigger={
          <Button
            variant={variant}
            size={size}
            disabled={disabled || loading}
            aria-label={t("common.splitButton.more", { name })}
            icon={<ChevronDown aria-hidden="true" />}
            // The two borders meet in one line in a secondary; in a primary they are the same
            // violet, so the seam is the darker one.
            className={cx("rounded-l-none px-0", MENU_WIDTH[size], primary ? "border-l-accent-hover" : "-ml-px")}
          />
        }
      >
        {menu}
      </Menu>
    </div>
  );
}
