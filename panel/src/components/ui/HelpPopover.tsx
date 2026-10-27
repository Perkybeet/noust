import type { ReactNode } from "react";

import { IconButton } from "./IconButton";
import { ICONS } from "./icons";
import { Popover } from "./Popover";

export interface HelpPopoverProps {
  /** Names the button for assistive technology and for a pointer that rests on it: "About deploy keys". Already translated. */
  label: string;
  /** A heading for the help, when it is more than a sentence. */
  title?: string;
  /** The help: a sentence or two, and a link to the documentation when there is more. */
  children: ReactNode;
  side?: "top" | "bottom" | "left" | "right";
  className?: string;
}

/**
 * The "i" beside a name that needs explaining: a small button that opens a Popover with the
 * explanation. It is a button and not a tooltip because a touch screen has no hover, a keyboard has
 * no pointer, and what is said here (what a thing is, what it will do) is not something to find by
 * resting on an icon. A Tooltip only names an icon.
 */
export function HelpPopover({ label, title, children, side = "bottom", className }: HelpPopoverProps) {
  return (
    <Popover
      side={side}
      {...(title !== undefined ? { title } : {})}
      {...(className !== undefined ? { className } : {})}
      trigger={<IconButton label={label} icon={<ICONS.info />} size="sm" tooltip={false} />}
    >
      {children}
    </Popover>
  );
}
